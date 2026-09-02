# CaP-X Pick-&-Place 复盘:抓取 / 放置的可靠做法(供 RoboTwin 复用)

> 面向:任何要在**纯视觉、无真值(no ground-truth pose)**下做 tabletop pick-and-place 的 agent(LIBERO / RoboTwin 同理)。
> 来源:逐行读 CaP-X 开源实现(`github.com/capgym/cap-x`)+ 在 LIBERO-PRO(ASPIRE 协议)上带 GT 实测对照,把「为什么 0%」挖到底。
> 基线:同一 LIBERO-PRO 上 OpenVLA / π₀ = 0%,π₀.₅ ≈ 13%,**CaP-Agent0 = 18%(纯视觉、无训练)**;ASPIRE ≈ 72%。
> 一句话:**CaP 的 18% 靠的不是某个神仙抓取网络,而是「可靠定位 + 干净点云 + 多候选校验 + 受控执行」这一整套工程纪律。**

---

## 0. 参考代码位置(CaP-X 开源)

| 内容 | 文件 |
|---|---|
| LIBERO 视觉 API(SAM3+GraspNet) | `capx/integrations/franka/libero.py` |
| 控制层(IK/goto_pose/gripper) | `capx/integrations/franka/control.py` |
| pick-place 全流程示例(agent 真跑的代码) | `capx/envs/tasks/franka/franka_pick_place.py` |
| 抓取示例(pregrasp+grasp+in-hand 循环) | `capx/envs/tasks/r1pro/r1pro_pickup_radio.py` |
| 点云清洗(DBSCAN) | `libero.py :: filter_noise` |
| Contact-GraspNet server | `capx/serving/launch_contact_graspnet_server.py` |
| 文档 | `docs/libero-tasks.md`, `docs/real-franka.md` |

---

## 1. CaP-X 的感知栈(最关键的一环)

CaP 的边界优势**不在抓取网络,在感知/定位的可靠性**。它的物体 3D 定位是:

1. **Molmo 精确指点** —— 用专门的 pointing 模型把「语言 → 图上一个点」,而**不是**让主 VLM 猜一个像素。(实测:LIBERO groceries 上「主 VLM 猜像素」判别正确物体 ≈ 1/7,SAM3 ≈ 4/7,VLM-point 是最弱信号。)
2. **SAM3 点提示分割**(point-prompt),point 拿不到就退回 **text-prompt** 分割。
3. **多视角**:`agentview` + `robot0_eye_in_hand`(腕),**两路都分割 + 取交集**,天然滤掉「整桌」这种伪 mask。
4. **`filter_noise` = DBSCAN**(`eps=0.005, min_samples=10`)清点云噪声。

> **教训 1(最重要)**:pick-place 的成败**首先取决于「有没有把 mask/点云限定在正确物体上」**。主 VLM 猜的像素是最弱信号;要用检测器(SAM3/OWLv2/Molmo)去指点,并且**对「整场景/整桌」的 mask 要拒绝或聚类清洗**。

---

## 2. CaP-X 的抓取(grasp)配方

`sample_grasp_pose(object_name, use_multiview=True)`(`libero.py`):

1. 多视角分割 → 物体点云 `pc_segment` + 全场景点云 `pc_full`(两相机深度反投影)。
2. `filter_noise(pc_segment)`。
3. Contact-GraspNet:`plan_grasp_from_point_clouds(pc_full, pc_segment, segmap_id=1)` → N 个 6-DoF 候选 + 分数。
4. **沿抓取轴 +0.12m 站位**:`SE3.from_translation([0,0,0.12])` → 返回 **(pregrasp_pose, grasp_pose)** 两段。
5. 选最高分:`grasp_scores.argmax()`;夹爪 +90° yaw 修正。

**执行(agent 真跑的代码,`r1pro_pickup_radio.py`):**
```python
pregrasp_poses, grasp_poses = sample_grasp_pose("red radio")
for i,(pregrasp,grasp) in enumerate(zip(pregrasp_poses, grasp_poses)):
    grasp_object(pregrasp, grasp, "red radio")   # 先到 pregrasp 站位 → 降到 grasp → 闭合
    if check_object_in_hand(): break             # 校验真的夹住了没有
    if i==2: break                               # 最多试 top-3 候选
```
- `goto_pose(pos, quat, z_approach)`:IK(PyRoKi)到完整 6-DoF 位姿,TCP 偏移 `[0,0,-0.1]`,`z_approach` 先到上方再受控插入。
- `close_gripper`:置 0 后步进 60 次。
- **注意:CaP 没有成功重试的复杂逻辑,靠「多候选 + in-hand 校验」保证可靠**——这才是可靠性来源,不是网络。

> **教训 2**:抓取要 **(a) pregrasp 站位再插入(别一步到位)**,**(b) 逐个试 top-K 候选 + in-hand 校验**,**(c) 用 6-DoF 姿态(别强制竖直)**。三者里 (a)(b) 是纯执行纪律,任何抓取网络都该配。

---

## 3. CaP-X 的放置(place)配方

`franka_pick_place.py`(agent 真跑的代码):
```python
# 抓起后先抬到安全高度,再横移
post_pick_pos = pick_pos.copy(); post_pick_pos[2] += 0.2
goto_pose(post_pick_pos, pick_quat)

# 用 bbox extent 精确算放置高度(堆叠/入容器都靠这个)
green_pos, _, _ = get_object_pose("green cube")
place_pos = green_pos.copy()
place_pos[2] = green_pos[2] + green_ext[2]/2 + red_ext[2]/2

goto_pose(place_pos, pick_quat, z_approach=0.1)   # 受控下降,复用抓取 quat
open_gripper()                                     # 释放
post_place_pos = place_pos.copy(); post_place_pos[2] += 0.1
goto_pose(post_place_pos, place_quat)              # 退让
```
硬规则(写在示例注释里):
- **抓起后先抬 ≥ +0.2m 再横移**(别拖着物体贴桌走)。
- **放置高度用 bbox extent 算**:`place_z = 目标中心z + 目标半高 + 物体半高`。
- **绝不从高处扔**:`z_approach` 受控下降。
- **放置朝向复用「抓取时」的 quat**,不要用 `get_object_pose` 的 quat(不可靠);没有就用 `(0,0,1,0)` wxyz(竖直向下)。
- 放完 **+0.1m 退让**。

**放置目标怎么定位(关键)**——`get_object_pose(name)`(`libero.py`)和抓取**共用同一套鲁棒感知**:
```
get_object_3d_points_and_masks_from_language(name, multiview)   # Molmo→SAM3→双视角交集
  → filter_noise(DBSCAN)                                        # 清噪
  → get_oriented_bounding_box_from_3d_points(points_3d)         # OBB
  → position = OBB.center ;  extent = OBB.extent(给放置高度)   # 纯视觉,无 GT
```
即:**放置目标位置 = 视觉分割目标点云的 OBB 中心**,高度用 OBB extent 算。**不是**「单像素反投影」也**不是**「被手臂挡住的头相机瞎猜」。

> **教训 3**:放置和抓取是**同一个感知问题的两次**——放置目标(盘子/容器)要用**和抓取一样鲁棒的多视角+滤噪+OBB 定位**(占 place 失败的大头),再**受控下降到用 OBB extent 算出的高度**,释放落在 OBB 中心,而不是「大概到上方就松手」。

---

## 4. 我们现在的实现 + 验证过的修复(LIBERO,保留 GraspGen)

> 我们复用 RoboTwin 的 **GraspGen**(不是 Contact-GraspNet),**不换网络**,只把「感知/执行纪律」按 CaP 补齐。全部 gated 在 `_fix_on()`(默认开,`ROBORSI_GRASP_FIX=0` 回退),**纯视觉零 GT**。

代码位置:
- 抓取原语:`roborsi/embodied/skills/base/grasp_object/libero/policy.py`
- 感知/点云/执行:`roborsi/embodied/skills/base/_lib/libero/_perception.py`
- 放置原语:`roborsi/embodied/skills/base/place_object_in/libero/policy.py`

### 4.1 抓取——已修好并转默认(A/B 实测,GT 仅用于测量)
| 病(验证到的) | 修 | 效果 |
|---|---|---|
| 信主 VLM 的 `find_pixel` 像素(最弱信号) | **改用 `localize_precise`(SAM3 优先)** | 抓点误差 **46cm → 4cm** |
| SAM 取最高 IoU mask = 常是整桌 | **选盖住指点、物体尺寸的 mask;只剩整桌就拒绝** | 抓对物体 **25% → 82%** |
| 点云带进桌面 → 质心偏半米 | **DBSCAN 保留最大簇 + 尺寸兜底拒绝** | 干净点云 **→ 100%** |
| 被拒 → VLM 空转耗预算 | **内部自纠:被拒时用检测器重定位再抓一次** | 不再空转 |
| 下压钳到「物体顶/鿠口」→ 闭空气 | **下压到物体中位(≤median),floor 在最低点上方** | 非碗物体能夹住 |
| 碗/宽口:竖直夹中心 = 抓空 | **yaw-sweep:抓不住时扫几个 yaw 让夹爪跨鿠壁 + in-hand 校验**(非-CGN 地几何) | 抬起率 **25% → 58%** |

**核心指标:抓取抬起率 25% → 58%。** 主链(定位→分割→抓点)已打通。

### 4.2 放置——尚未修(这是当前瓶颈,0% 任务成功的原因)
现状 `place_object_in`:vision 定位目标 → `object_cloud` 取质心 + rim_z → hover→降→open。
**为什么还失败(日志实证)**:
- 放置目标(盘子)定位**崩**:`(128,128) sentinel` / `unproject garbage` —— **手臂/被夹物体遮挡盘子** → 47 次 place 只 8 次 released。
- 即便 released,**物体没落在盘子上** → 判据 0/3(VLM 自以为成功)。
- 每集烧 ~700s / 29 步空转。
- **根因**:place 这条链**没吃到 §4.1 给 grasp 的那套鲁棒性**——还是老的直调 `localize_precise`,没有自纠、没有遮挡清除后重试、释放点不保证落在目标中心。

### 4.3 放置该怎么修(镜像 grasp,照 CaP §3)
1. **目标定位鲁棒化 = 抓取那套照搬到 place**:SAM3 优先 + 自纠重定位 + sentinel/遮挡时**抬臂清视野再感知**;取目标点云的 **OBB 中心**作放置 xy(照 CaP `get_object_pose`),别用单像素反投影。
2. **抬高再横移**:place 前先抬 ≥ +0.2m(减少遮挡 + 别拖着物体贴桌)。
3. **受控下降到算出的高度**:`place_z = 目标点云表面/OBB 顶 z + 物体半高`,`z_approach` 式受控降,**别从高处松手**。
4. **落到目标 OBB 中心**:释放 xy = 目标点云质心/OBB 中心,而非当前臂位。
5. **释放 quat**:复用抓取 quat 或 `(0,0,1,0)` 竖直向下。

> 现状 `place_object_in` 用的是 `object_cloud` 的 median 质心 + `rim_z`(85 分位)——比 CaP 的 OBB 粗,且**目标定位没吃到 §4.1 的自纠/遮挡处理**,这是 place 还 0% 的直接原因。

---

## 5. 从 CaP-X 学到的可迁移「工程纪律」清单(RoboTwin 直接照抄)

1. **定位 > 网络**:先保证 mask/点云锁在**正确物体**上。用检测器指点(SAM3/OWLv2/Molmo),别信主 VLM 猜的像素。
2. **拒绝整场景 mask**:SAM 的最高-IoU mask 常是整桌;按「盖住指点 + 物体尺寸」选,选不出就**拒绝并重感知**,而不是拿脏点云去抓。
3. **DBSCAN 清点云**:保留离指点最近/最大的簇,丢桌面裙边。
4. **多视角融合**(有腕/多相机时):补全几何,尤其碗这类单视角只看到鿠壳的物体。(注:LIBERO 腕相机抓取时未必看向物体,收益有限——**先确认相机真看到物体**再融合。)
5. **多候选 + in-hand 校验**:top-K 抓取逐个试,proprio(夹爪间隙)确认夹住了才继续。
6. **pregrasp 站位 + 受控插入**:沿抓取轴留 standoff,先到上方再降,别一步到位。
7. **下压到物体体内**,别停在顶/鿠口(否则闭空气)。
8. **放置 = 第二次感知问题**:目标同样要可靠定位;**抬高再横移**;**bbox/点云算高度**;**z_approach 受控下降**;**绝不从高处扔**;释放落到目标中心;复用抓取 quat。
9. **失败要「拒绝 + 自纠」,不要「弹回 VLM 空转」**:感知失败时内部换检测器重定位重试,别把 budget 耗在 re-perceive 循环里。
10. **用 GT 只做「测量/诊断」不做「驱动」**:验证时读真值算误差(抓点 vs 物体、下压 vs 物体高),但策略本身一格真值都不用。这套 diag(`ROBORSI_GRASP_DEBUG=1`)是定位病根的关键工具。

---

## 6. 实测方法论(怎么把「为什么不行」挖到底)

不要靠读代码猜。加一段 **GT 仅用于测量** 的诊断(gated `ROBORSI_GRASP_DEBUG=1`),每次抓取记录并聚合:
- `loc_err`(指点像素反投影 → 物体)、`centroid_err`(SAM 点云质心 → 物体)、`grasp_err`(GraspGen 点 → 物体)—— **分层定位是哪一环崩**。
- `cloud_pts`(点云大小)—— 干净物体 ~700 点,整桌 7k–65k,一眼看出 mask blow-up。
- `gap`(夹爪闭合间隙)、`descend_vs_obj`(下压 vs 物体高)、`lifted`(物体 z 抬升)。

正是这套让我们发现:**「远不如 CaP」的主因是 SAM mask 抠整桌 → 抓点偏 46cm**,而不是抓取网络本身。**先量,再修,每修一层复测**——修好一层会暴露下一层(mask → 下压 → 碗执行 → place)。
