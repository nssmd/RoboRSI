# Real-Robot Porting: Tool / Endpoint Spec

> 把这套 sim-tested VLM+tool stack 接到真机（aloha-agilex / Franka / UR / etc.）
> 需要把哪些 base skill 的 RoboTwin 仿真实现换成真机驱动。本文档逐 skill 列出
> 期望的输入/输出 schema 和需要的真机端口（control / perception / sensors）。

## 总体架构

```
VLM (Claude/GPT)
   ↓ tool calls
rollout_runtime._dispatch   ← 工具调度（保持不变）
   ↓
base skill .policy.py        ← 平台特异（每个机器人重新实现）
   ↓ sim                            ↓ real
RoboTwin (sapien)            ROS2 / robot SDK / 直接 USB
```

**移植边界**：`rollout_runtime` + `_State` + tool 调度逻辑（保持原样）；
每个 `roborsi/embodied/skills/base/<robot>/<skill>/policy.py` 重写。

---

## 必须实现的 base skill（按优先级）

### Tier 1 · 控制 ⚙️

| Skill | 输入 | 输出 | 真机端口 |
|---|---|---|---|
| `move_to_pose` | `arm`, `x, y, z, quat` (FLANGE world target) | `ok, ee_after, delta_m, target_dist_m` | ROS2 service `/{arm}/plan_to_pose` (cuRobo / MoveIt / pinocchio IK) → joint trajectory streamer。**必须返回执行后实际 EE pose**（不只 plan success）。 |
| `move_fingertip_to` | `arm`, `x, y, z, quat` (FINGERTIP TCP) | 同上 | 同 `move_to_pose` + flange↔TCP 偏移转换（见 gripper_geom）。 |
| `gripper` | `arm`, `action=open/close`, `pos?` | `ok` | ROS2 topic `/{arm}/gripper_cmd`（aloha 是 fl/fr_link7-8 prismatic joint）。**必须能查实际 jaw 位置**。 |
| `home` | `arm` | `ok` | 预存 home qpos，调用 `move_to_pose` 或 joint command。 |
| `read_joint_state` | `arm` | `qpos`, `qvel` | ROS2 topic `/joint_states` 订阅。 |
| `get_arm_pose` | `arm` | `xyz, quat, fingertip_xyz_top_down` | FK：从 joint_state 算 EE link 位姿。fingertip_xyz_top_down 用 mesh 测的 `ALOHA_TCP_IN_EE_LOCAL=0.1556`。 |

### Tier 2 · 感知 👁️

| Skill | 输入 | 输出 | 真机端口 |
|---|---|---|---|
| `look` / `capture_image` | `camera?` | RGB jpg path, shape | 多相机：head/front/left_wrist/right_wrist。ROS2 topic `/camera/{name}/rgb`。 |
| `unproject_pixel` | `u, v, camera?` | world `xyz` | 需 `/camera/{name}/depth` + intrinsics + extrinsics（手眼标定）。**真机最大坑**：手眼标定误差直接 propagate。 |
| `find_pixel` | `object` (noun phrase) | `u, v, bbox, confidence` | Grounded-SAM（可本地部署 GroundingDINO + SAM2，或调用 Foundation-Stereo / FoundationPose）。 |
| `get_object_bbox` | `object` | `bbox, centroid, width_px, height_px` | 同 `find_pixel`，只返 bbox。 |
| `detect_object` | `object` | `dets[]` (multi-instance) | 同上。 |
| `label_points_grid` | `mask_from_query?, grid_n` | 画了 N 个 numbered point 的图 + labels | SAM mask → 内部均匀采点 → cv2 画到图上。纯 perception 后处理，无新端口。 |
| `localize_object_top_center` | `object, grid_n` | `xyz` (亚厘米) | 编排上面三步 + sub-VLM 调用挑最准点 + top-band z 修正。**必需 sub-VLM endpoint**（recommend Qwen2.5-VL-72B 本地部署，rollout 用的）。 |
| `verify_holding_visual` | `arm, object` | `holding_visual: bool` | SAM 检测物体 + 深度查 z > table + 像素距离 EE 投影 < 60px。**需要相机 + 已知 table z 基线**。 |
| `is_holding` | `arm` | `bool` | 真机：用 gripper 关节 encoder 查 jaw 间距 vs commanded position（差异 > 1mm = 有东西）。或用 fingertip 力传感器（ATI mini40 / Robotiq force-torque）。 |
| `scan_wrist` | `arm` | wrist-camera jpg | 直接读 wrist camera ROS topic。 |
| `zoom_in` | `u, v` | crop+upscale 图 | 纯 OpenCV，无新端口。 |

### Tier 3 · 几何 / 抓取 / 规划 🧮

| Skill | 输入 | 输出 | 真机端口 |
|---|---|---|---|
| `get_grasp_pose` | `object`, `u, v?`, `z_min?, z_max?, half_window_px?` | `grasp_pose [x,y,z,qx,qy,qz,qw], world_xyz, score` | **GraspGen / Contact-GraspNet 模型服务**（已有 client-server 框架，见 `$GRASPGEN_REPO/client-server/`），输入点云 + bbox，输出 6-DoF grasp。 |
| `get_grasp_pose_segmented` | `object` | `candidates[]` with TCP/flange/quat | 同上，加 SAM mask 约束。 |
| `grasp_then_lift` | `arm, object, x, y, z, object_height_m, object_radius_m` | `ok, holding_visual, descend_z_used` | 编排：open → hover → descend 1cm-步进 → close → lift → verify。**default heights 用 mesh 规则反推**（finger length=0.071m 等都从 mesh 实测）。 |
| `grasp_object` | `arm, object` | `ok, attempts, holding_visual` | 同上 + 自动 GraspNet 候选筛选 + bbox 过滤 + IK precheck。 |
| `place_object_in` | `arm, target_object, drop_height_m?` | `ok` | 编排：到目标上方 → 开夹爪。 |
| `tap_held_on_target` | `arm, tool_query, target_x/y/z` | `ok, contact_pair, descended_m` | **真机最复杂**：需 (a) multi-view tip perception (b) iterative XY correction (c) descend-until-contact loop。真机用力传感器检测 contact，不能用 sim 的 `scene.get_contacts`。 |
| `move_to_pixel` | `arm, u, v, action=hover/grasp/release` | `ok, ee_xyz` | unproject_pixel + move_to_pose 组合。 |
| `is_reachable` | `arm, x, y, z` | `reachable, distance_to_base` | **必须查 URDF base link 真实世界位置**（不是写死 `(±0.18, 0, 0.85)`）—— `is_reachable/policy.py` 已通过 sapien link query 实现，真机版用 `tf2` 查 base frame。 |
| `home` / `recall_past_success` / `list_contacts` | — | — | `list_contacts` 真机用力传感器/RGBD 替代；其它 trivial。 |

### Tier 4 · 测量 / VLM 辅助（纯软件，无真机端口）

`measure_distance` / `measure_vector` / `measure_relative_rotation` / `rotate_vector` /
`estimate_feature_point`：纯 numpy 计算，**真机直接复用 sim 实现**，无需重写。

### Tier 5 · 学习策略（可选）

`execute_with_pi05`：调 π₀.₅ VLA inference server。真机需 GraspGen 之外另起 VLA 服务。

---

## sim ↔ real 关键映射

### 1. 坐标系
- **世界系**：sim 用 sapien world frame (x 右, y 前, z 上)。真机需要标定 base frame 与世界系的变换。建议以 robot footprint 为世界系原点。
- **末端系**：aloha-agilex flange (fl_link6 / fr_link6)；fingertip TCP = flange + 0.1556 m 沿 EE local +X (mesh 实测，见 `gripper_geom.py`)。

### 2. 抓握稳定（sim 才有的 hack）
sim 用 `gripper_attach.py` 的 sapien drive lock 解决摩擦不够的问题。**真机不需要这个**：真夹爪有：
- 闭环力控（grip force feedback）
- 柔顺指垫（rubber pad）
- tactile sensor 检测 slip

真机要写：闭合后用 力/位置阈值确认 grip → 若 slip detected 重抓。

### 3. 接触检测
sim：`impl.scene.get_contacts()` 直接拿。
真机：力传感器（wrist FT 或 fingertip FSR）。`tap_held_on_target` 的 descend-until-contact 改成 "descend-until-force-spike"。

### 4. 物理保护
sim 撞坏物体没事。真机要加：
- joint limit / velocity / acc soft constraint
- collision check via cuRobo
- emergency stop (E-stop) hardware

---

## 推荐部署栈

| 组件 | 推荐 | 端口 |
|---|---|---|
| Robot SDK | ROS2 Humble + aloha-agilex driver | DDS |
| Motion plan | cuRobo (sim 已经用) | python lib |
| Perception | GroundingDINO + SAM2 (本地 GPU) | gRPC / FastAPI :5557 |
| Grasp model | GraspGen server (已有) | TCP :5556 |
| Depth | FoundationStereo (zero-shot) 或 RealSense D435 / D455 内建 | ROS2 topic |
| Sub-VLM (set-of-mark) | Qwen2.5-VL-72B local 或 GPT-5/Claude API | HTTP :8000 / Azure / Anthropic |
| Main VLM (orchestrator) | Claude Opus 4.7 或 GPT-5.1 | Anthropic / Azure |

---

## 移植 checklist

```
[ ] Tier 1 控制 (6 skills) ← 必须，全部重写
[ ] Tier 2 感知 (11 skills) ← 必须，重写 unproject/find_pixel/verify_holding
[ ] Tier 3 抓取/规划 (9 skills) ← 必须，重写大部分
[ ] Tier 4 测量 (5 skills) ← 复用 sim 实现
[ ] Tier 5 VLA (1 skill) ← 可选
[ ] gripper_attach.py 不要移植 ← sim-only hack
[ ] gripper_geom.py mesh 常量 ← 重新测当前真机的 flange→TCP 偏移
[ ] is_reachable / place_object_in 的 base pose ← 改用 tf2 查
[ ] 手眼标定 ← 全流程精度的最大瓶颈
```
