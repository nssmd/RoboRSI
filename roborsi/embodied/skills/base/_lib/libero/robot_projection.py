"""Project known robot geometry from coherent measured robot sensor frames.

Only robot-only MJCF is accepted from the adapter's KnownRobotGeometry API.
Camera conventions follow robosuite.camera_utils: camera_to_world has CV axes;
MuJoCo cameras have GL axes, hence the fixed diag(1,-1,-1) rotation below.
"""
from dataclasses import dataclass
import hashlib
import math
import xml.etree.ElementTree as ET
import numpy as np


@dataclass(frozen=True)
class RobotProjection:
    frame_token: str
    robot_depth_m: np.ndarray
    robot_mask: np.ndarray
    geometric_mask: np.ndarray
    depth_tolerance_m: float


class RobotOnlyRenderer:
    def __init__(self, geometry, calibration):
        import mujoco
        from scipy.spatial.transform import Rotation
        self._mj=mujoco
        self._geometry=geometry
        self._reset=geometry.reset_generation
        self._height=int(calibration.image_height)
        self._width=int(calibration.image_width)
        self._renderer=None
        self._closed=False
        self._intrinsic=np.asarray(calibration.intrinsic,dtype=float).copy()
        self._extrinsic=np.asarray(calibration.camera_to_world,dtype=float).copy()
        self._near=float(calibration.near_m);self._far=float(calibration.far_m)
        self._camera_contract=(calibration.camera_name,self._height,self._width,self._near,self._far,calibration.row_order,calibration.pixel_order,calibration.depth_convention)
        if calibration.row_order!='top_down' or calibration.pixel_order!='uv_col_row' or calibration.depth_convention!='metric_camera_depth_m':
            raise ValueError('Unsupported sensor camera convention')
        k=self._intrinsic
        # The actual LIBERO head camera is centered with square pixels. Refuse
        # unsupported cameras rather than silently render a mismatched frustum.
        expected=np.array([[k[1,1],0,self._width/2],[0,k[1,1],self._height/2],[0,0,1]])
        if k.shape!=(3,3) or not np.allclose(k,expected,rtol=0,atol=1e-6) or k[1,1]<=0:
            raise ValueError('Robot renderer requires the verified centered square-pixel LIBERO camera')
        root=ET.fromstring(geometry.robot_model_xml)
        if root.tag!='mujoco':raise ValueError('Expected robot-only MJCF')
        world=root.find('worldbody')
        if world is None:raise ValueError('Robot model has no worldbody')
        # The API source supplies a robot_model, not full scene XML. Additionally
        # reject free joints and unexpected articulated DOFs after compilation.
        if root.findall('.//freejoint'):raise ValueError('Robot-only model unexpectedly contains free joints')
        statistic=root.find('statistic')
        if statistic is None:statistic=ET.SubElement(root,'statistic')
        statistic.set('extent','1')
        visual=root.find('visual')
        if visual is None:visual=ET.SubElement(root,'visual')
        mapping=visual.find('map')
        if mapping is None:mapping=ET.SubElement(visual,'map')
        mapping.set('znear',str(self._near));mapping.set('zfar',str(self._far))
        global_=visual.find('global')
        if global_ is None:global_=ET.SubElement(visual,'global')
        global_.set('offwidth',str(self._width));global_.set('offheight',str(self._height))
        transform=self._extrinsic.copy()
        transform[:3,:3]=transform[:3,:3]@np.diag([1.,-1.,-1.])
        q=Rotation.from_matrix(transform[:3,:3]).as_quat()[[3,0,1,2]]
        ET.SubElement(world,'camera',name='roborsi_known_head',mode='fixed',pos=' '.join(map(str,transform[:3,3])),quat=' '.join(map(str,q)),fovy=str(math.degrees(2*math.atan(self._height/(2*k[1,1])))))
        self._model=mujoco.MjModel.from_xml_string(ET.tostring(root,encoding='unicode'))
        names=tuple(geometry.arm_joint_names)+tuple(geometry.gripper_joint_names)
        if len(names)!=len(set(names)) or self._model.nq!=len(names):raise ValueError('Known robot joint mapping is not one-to-one')
        if {self._model.joint(i).name for i in range(self._model.njnt)}!=set(names):raise ValueError('Unexpected non-robot joint in known model')
        root_id=self._model.body(geometry.root_body).id
        def descendant(body_id):
            while body_id:
                if body_id==root_id:return True
                body_id=int(self._model.body_parentid[body_id])
            return False
        visual_names=set(geometry.visual_geom_names);contact_names=set(geometry.contact_geom_names)
        compiled_names=[self._model.geom(i).name for i in range(self._model.ngeom)]
        if len(set(compiled_names))!=len(compiled_names) or any(not n for n in compiled_names):raise ValueError('Duplicate or unnamed known robot geom')
        unknown=set(compiled_names)-(visual_names|contact_names)
        if unknown:raise ValueError('Geometry outside verified robot geom manifest: '+repr(sorted(unknown)))
        for i,name in enumerate(compiled_names):
            if not descendant(int(self._model.geom_bodyid[i])):raise ValueError('Geom outside known robot root: '+name)
            enabled=int(self._model.geom_group[i])==1
            if enabled!=(name in visual_names):raise ValueError('Robot visual/contact render group mismatch: '+name)
        for i in range(self._model.njnt):
            if not descendant(int(self._model.jnt_bodyid[i])):raise ValueError('Joint outside known robot root')
        self._data=mujoco.MjData(self._model)
        self._renderer=mujoco.Renderer(self._model,height=self._height,width=self._width)
        self._renderer.enable_depth_rendering()
        self._option=mujoco.MjvOption();self._option.geomgroup[:]=0;self._option.geomgroup[1]=1
        self.geometry_sha256=hashlib.sha256(geometry.robot_model_xml.encode()).hexdigest()

    def render(self,frame,*,depth_tolerance_m=0.008,dilation_pixels=2):
        import cv2
        if self._closed:raise RuntimeError('Robot renderer closed')
        if not frame.valid or frame.reset_generation!=self._reset:raise ValueError('Invalid or stale sensor frame')
        if frame.robot_state.observation_generation!=frame.observation_generation:raise ValueError('Mixed robot/frame generation')
        cal=frame.calibration
        contract=(cal.camera_name,int(cal.image_height),int(cal.image_width),float(cal.near_m),float(cal.far_m),cal.row_order,cal.pixel_order,cal.depth_convention)
        if contract!=self._camera_contract:raise ValueError('Camera convention or clipping range changed; recreate renderer')
        if not np.allclose(cal.intrinsic,self._intrinsic,rtol=0,atol=1e-9) or not np.allclose(cal.camera_to_world,self._extrinsic,rtol=0,atol=1e-9):raise ValueError('Camera calibration changed; recreate renderer')
        if not (0<float(depth_tolerance_m)<=0.02) or dilation_pixels not in (0,1,2,3):raise ValueError('Invalid robot exclusion uncertainty')
        robot=frame.robot_state
        names=tuple(robot.arm_joint_names)+tuple(robot.gripper_joint_names)
        if names!=tuple(self._geometry.arm_joint_names)+tuple(self._geometry.gripper_joint_names):raise ValueError('Joint ordering changed')
        for name,value in zip(names,tuple(robot.arm_qpos)+tuple(robot.gripper_qpos)):
            if not np.isfinite(value):raise ValueError('Nonfinite measured robot joint')
            self._data.joint(name).qpos[:]=value
        self._mj.mj_forward(self._model,self._data)
        body=self._data.body(self._geometry.root_body)
        if not np.allclose(body.xpos,robot.base_position_world,rtol=0,atol=1e-5):raise ValueError('Known robot mount differs from measured base')
        q=np.asarray(robot.base_quaternion_wxyz)
        if q.shape!=(4,) or not np.all(np.isfinite(q)) or not np.isclose(np.linalg.norm(q),1,rtol=0,atol=1e-5):raise ValueError('Measured base quaternion is not finite and unit length')
        q=q/np.linalg.norm(q)
        if abs(float(np.dot(body.xquat,q)))<1-1e-7:raise ValueError('Known robot base orientation differs')
        self._renderer.update_scene(self._data,camera='roborsi_known_head',scene_option=self._option)
        # Depth-buffer background is not guaranteed to round-trip to exactly the
        # configured far plane. Classifying geometry with a far-depth threshold
        # can therefore mark the entire image as robot geometry. Obtain the
        # silhouette from MuJoCo segmentation instead; this renderer contains
        # only the manifest-validated robot, attached mount, and gripper geoms.
        self._renderer.disable_depth_rendering()
        self._renderer.enable_segmentation_rendering()
        try:
            segmentation=np.array(self._renderer.render(),copy=True)
        finally:
            self._renderer.disable_segmentation_rendering()
            self._renderer.enable_depth_rendering()
        depth=np.array(self._renderer.render(),dtype=float,copy=True)
        measured=np.asarray(frame.depth_m,dtype=float)
        if depth.shape!=measured.shape or depth.shape!=(self._height,self._width):raise ValueError('Robot/sensor pixel shape mismatch')
        if segmentation.shape!=(self._height,self._width,2):raise ValueError('Robot segmentation pixel shape mismatch')
        geometric=(segmentation[...,0]>=0)&(segmentation[...,1]==int(self._mj.mjtObj.mjOBJ_GEOM))
        visible=geometric&np.isfinite(measured)&(np.abs(depth-measured)<=float(depth_tolerance_m))
        if dilation_pixels:
            size=2*dilation_pixels+1;visible=cv2.dilate(visible.astype(np.uint8),np.ones((size,size),dtype=np.uint8)).astype(bool)
        for a in (depth,geometric,visible):a.setflags(write=False)
        return RobotProjection(frame.frame_token,depth,visible,geometric,float(depth_tolerance_m))

    def close(self):
        if self._renderer is not None:self._renderer.close();self._renderer=None
        self._closed=True
