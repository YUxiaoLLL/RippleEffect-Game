import * as THREE from 'https://cdn.skypack.dev/three@0.132.2';
import { OrbitControls } from 'https://cdn.skypack.dev/three@0.132.2/examples/jsm/controls/OrbitControls.js';
import { TransformControls } from 'https://cdn.skypack.dev/three@0.132.2/examples/jsm/controls/TransformControls.js';
import { SSAOPass } from 'https://cdn.skypack.dev/three@0.132.2/examples/jsm/postprocessing/SSAOPass.js';
import { EffectComposer } from 'https://cdn.skypack.dev/three@0.132.2/examples/jsm/postprocessing/EffectComposer.js';
import { RenderPass } from 'https://cdn.skypack.dev/three@0.132.2/examples/jsm/postprocessing/RenderPass.js';

const CITY_ROT_DEG = 90;
const CITY_ROT_RAD = THREE.MathUtils.degToRad(CITY_ROT_DEG);

// --- Basic Scene Setup ---
console.log("App Version: 1.2 (Layer Fixes) - Loaded " + new Date().toISOString());
const scene = new THREE.Scene();
const cityGroup = new THREE.Group();
scene.add(cityGroup);

let globalClippingPlanes = [];

function updateClippingPlanes(width, depth) {
  const w = width / 2;
  const d = depth / 2;
  
  // Create 4 planes facing inward, aligned EXACTLY with the board edges
  globalClippingPlanes = [
    new THREE.Plane(new THREE.Vector3(1, 0, 0), w),   // Left limit
    new THREE.Plane(new THREE.Vector3(-1, 0, 0), w),  // Right limit
    new THREE.Plane(new THREE.Vector3(0, 0, 1), d),   // Back limit
    new THREE.Plane(new THREE.Vector3(0, 0, -1), d)   // Front limit
  ];
}

/**
 * 核心函数：根据建筑群生成完美的圆角沙盘
 */
function updateSceneVisuals() {
    // 1. 计算极点 (Bounding Box) - 仅基于建筑
    // 确保世界矩阵已更新，以便获取正确的旋转后包围盒
    cityGroup.updateMatrixWorld(true);
    
    const box = new THREE.Box3();
    // 使用 expandByObject 避免将 mesh 从原场景中移除
    buildings.forEach(b => box.expandByObject(b));

    // 加一点点边距 (Padding)，比如 5%
    const size = new THREE.Vector3();
    box.getSize(size);
    const padding = 0.05; 
    box.expandByVector(size.multiplyScalar(padding));

    const minX = box.min.x;
    const maxX = box.max.x;
    const minZ = box.min.z;
    const maxZ = box.max.z;

    const width = maxX - minX;
    const depth = maxZ - minZ;
    const center = new THREE.Vector3();
    box.getCenter(center);

    console.log(`沙盘范围: ${width.toFixed(0)} x ${depth.toFixed(0)}`);

    // 2. 应用裁剪 (Clipping) - “罩子”
    // 凡是超出 min/max 范围的绿化、水体、道路，全部切掉
    // 注意：Plane 的常数项是 -distanceToOrigin
    globalClippingPlanes = [
        new THREE.Plane(new THREE.Vector3(1, 0, 0), -minX),
        new THREE.Plane(new THREE.Vector3(-1, 0, 0), maxX),
        new THREE.Plane(new THREE.Vector3(0, 0, 1), -minZ),
        new THREE.Plane(new THREE.Vector3(0, 0, -1), maxZ)
    ];

    scene.traverse((obj) => {
        if (obj.isMesh) {
            // 排除掉我们即将生成的板子本身，只切场景元素
            if (obj.name !== 'BaseBoard') {
                // 确保是个材质对象
                if (obj.material) {
                   obj.material.clippingPlanes = globalClippingPlanes;
                   obj.material.clipShadows = true;
                }
            }
        }
    });

    // 3. 生成圆角板子 (Visual Polish)
    createCleanRoundedBoard(width, depth, center);
    
    // 更新阴影相机范围以匹配新沙盘
    const maxDim = Math.max(width, depth);
    const SHADOW_SIZE = maxDim * 0.8;
    directionalLight.shadow.camera.left   = -SHADOW_SIZE;
    directionalLight.shadow.camera.right  =  SHADOW_SIZE;
    directionalLight.shadow.camera.top    =  SHADOW_SIZE;
    directionalLight.shadow.camera.bottom = -SHADOW_SIZE;
    directionalLight.shadow.camera.updateProjectionMatrix();
}

/**
 * 创建干净、高级的圆角板子 (无黑边)
 */
function createCleanRoundedBoard(width, depth, center) {
    // 清理旧板子
    const oldBoard = scene.getObjectByName('BaseBoard');
    if (oldBoard) {
        scene.remove(oldBoard);
        // 如果在 cityGroup 里也要移除
        cityGroup.remove(oldBoard); 
    }

    // 定义圆角形状
    const shape = new THREE.Shape();
    const w = width;
    const h = depth;
    const radius = 20; // 圆角半径

    // 绘制以 (0,0) 为中心的圆角矩形
    // 注意：ExtrudeGeometry 默认挤压是在 XY 平面，所以我们画的时候是 XY
    // 但最后我们要放到 XZ 平面上，所以这里 width 对应 x，depth 对应 y (将被旋转为 z)
    const x = -w / 2;
    const y = -h / 2;
    
    shape.moveTo(x + radius, y);
    shape.lineTo(x + w - radius, y);
    shape.quadraticCurveTo(x + w, y, x + w, y + radius);
    shape.lineTo(x + w, y + h - radius);
    shape.quadraticCurveTo(x + w, y + h, x + w - radius, y + h);
    shape.lineTo(x + radius, y + h);
    shape.quadraticCurveTo(x, y + h, x, y + h - radius);
    shape.lineTo(x, y + radius);
    shape.quadraticCurveTo(x, y, x + radius, y);

    // 挤压出厚度 (Extrude)
    const geometry = new THREE.ExtrudeGeometry(shape, {
        steps: 1,
        depth: 10,       // 板子厚度
        bevelEnabled: true, // 开启倒角 (Bevel) 代替黑边
        bevelThickness: 1,
        bevelSize: 1,
        bevelSegments: 2
    });
    
    // 旋转几何体，使其平躺
    geometry.rotateX(-Math.PI / 2);

    // 关键修复：计算几何体包围盒，并将其向下平移，确保最高点位于 y = -0.2
    // 这样就不会遮挡住 y=0.1 的水面和 y=0.3 的道路
    geometry.computeBoundingBox();
    const bbox = geometry.boundingBox;
    const topY = bbox.max.y;
    const targetTopY = -0.2;
    const yOffset = targetTopY - topY;
    geometry.translate(0, yOffset, 0);

    // 材质：浅色木纹 / 哑光白
    const material = new THREE.MeshStandardMaterial({
        color: 0xD2B48C, // 保持之前的浅木色，或者换成 0xf5f5f5
        roughness: 0.8,
        metalness: 0.1
    });

    const board = new THREE.Mesh(geometry, material);
    board.name = 'BaseBoard';
    
    // 调整位置：因为 geometry 已经被 translate 修正了高度，这里只需要设置 XZ
    board.position.set(center.x, 0, center.z); 
    board.receiveShadow = true;

    scene.add(board);
}

const aspect = window.innerWidth / window.innerHeight;
const frustumSize = 500;
const camera = new THREE.OrthographicCamera(
  (frustumSize * aspect) / -2,
  (frustumSize * aspect) / 2,
  frustumSize / 2,
  frustumSize / -2,
  0.1,
  2000
);

const renderer = new THREE.WebGLRenderer({
  antialias: true,
  stencilBuffer: false // Disable stencil as we are switching to Clipping Planes
});
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.localClippingEnabled = true; // ✅ Enable Local Clipping

renderer.setClearColor(0x1a1a1a); // 极简深灰背景
renderer.setSize(window.innerWidth, window.innerHeight);
document.body.appendChild(renderer.domElement);

scene.fog = new THREE.Fog(0x1a1a1a, 800, 2000); // 雾气颜色匹配背景

const renderTarget = new THREE.WebGLRenderTarget(
  window.innerWidth,
  window.innerHeight,
  {
    minFilter: THREE.LinearFilter,
    magFilter: THREE.LinearFilter,
    format: THREE.RGBAFormat,
    stencilBuffer: true,   // ★★★ 关键！
    depthBuffer: true
  }
);

const composer = new EffectComposer(renderer, renderTarget);
composer.addPass(new RenderPass(scene, camera));

const ssao = new SSAOPass(scene, camera, window.innerWidth, window.innerHeight);
ssao.kernelRadius = 8;
ssao.minDistance = 0.005;
ssao.maxDistance = 0.1;
ssao.output = SSAOPass.OUTPUT.Default;
ssao.clear = false; // 关键：保留 stencil/depth
composer.addPass(ssao);

// 验证 stencil buffer 是否成功开启
console.log('✅ Stencil Buffer 状态检查:');
console.log('  - renderer.stencilBuffer:', renderer.capabilities.stencil);
console.log('  - composer.renderTarget1.stencilBuffer:', composer.renderTarget1.stencilBuffer);
console.log('  - composer.renderTarget2.stencilBuffer:', composer.renderTarget2.stencilBuffer);

// --- Controls ---
const controls = new OrbitControls(camera, renderer.domElement);

// Enable Left Click Pan
controls.mouseButtons = {
	LEFT: THREE.MOUSE.PAN,
	MIDDLE: THREE.MOUSE.DOLLY,
	RIGHT: THREE.MOUSE.ROTATE
}

let lastTarget = new THREE.Vector3();
lastTarget.copy(controls.target);

let boardHalfSize = 0;

controls.addEventListener('change', () => {
  if (!boardHalfSize) return;

  const t = controls.target;
  const offset = camera.position.clone().sub(t);

  t.x = THREE.MathUtils.clamp(t.x, -boardHalfSize, boardHalfSize);
  t.z = THREE.MathUtils.clamp(t.z, -boardHalfSize, boardHalfSize);

  camera.position.copy(t.clone().add(offset));
});

controls.enableRotate = false;
controls.enablePan = true;
controls.minZoom = 0.3;
controls.maxZoom = 10;

const transformControls = new TransformControls(camera, renderer.domElement);

// --- Time & Location (London) ---
const SITE_LAT = 51.5074;
const SITE_LON = -0.1278;

let simTime = new Date('2024-06-21T12:00:00');
const TIME_SPEED = 900;  // 1 秒 = 10 分钟
let isPaused = false;

const clock = new THREE.Clock();

// UI 引用
const dateInput  = document.getElementById('date-input');
const timeInput  = document.getElementById('time-input');
const timeLabel  = document.getElementById('time-label');
const pauseButton = document.getElementById('pause-button');

if (pauseButton) {
  pauseButton.addEventListener('click', () => {
    isPaused = !isPaused;
    pauseButton.textContent = isPaused ? '▶' : '⏸';
    pauseButton.style.background = isPaused ? '#4CAF50' : '#555'; // Green when paused (ready to play), Gray when playing
  });
}
scene.add(transformControls);

// --- Socket.IO Sync (Multiplayer) ---
const urlParams = new URLSearchParams(window.location.search);
const roomId = urlParams.get('roomId');
const playerId = urlParams.get('playerId');
let socket = null;

if (window.io && roomId) {
    console.log(`[3D Sync] Connecting to room ${roomId}...`);
    socket = window.io({
        query: { roomId, playerId }
    });

    socket.on('connect', () => {
        console.log('[3D Sync] Connected!');
        socket.emit('join_room_socket', { roomId });
    });

    /* 
    // User Request: Disable Sync
    socket.on('scene_object_updated', (data) => {
        if (data.playerId === playerId) return; // Skip own updates

        console.log('[3D Sync] Remote update:', data);
        // Find mesh by FID (OS ID) or internal ID
        const mesh = buildings.find(b => {
            const fid = b.userData.fid || String(b.userData.id);
            return fid === data.objectId;
        });

        if (mesh) {
            if (data.transform.position) mesh.position.copy(data.transform.position);
            if (data.transform.scale) mesh.scale.copy(data.transform.scale);
            if (data.transform.rotation) {
                mesh.rotation.set(
                    data.transform.rotation.x,
                    data.transform.rotation.y,
                    data.transform.rotation.z
                );
            }
            mesh.updateMatrix();
            // Re-compute bounding box if needed, or visual updates
        }
    });
    */
}

function emitObjectUpdate(mesh) {
    if (!socket || !roomId) return;
    
    const id = mesh.userData.fid || String(mesh.userData.id);
    const transform = {
        position: mesh.position,
        scale: mesh.scale,
        rotation: { x: mesh.rotation.x, y: mesh.rotation.y, z: mesh.rotation.z }
    };

    socket.emit('update_scene_object', {
        roomId,
        playerId,
        objectId: id,
        transform
    });
}

transformControls.addEventListener('dragging-changed', e => {
  controls.enabled = !e.value;
  
  if (e.value) {
      // Drag Start
      if (selectedBuilding) pushHistory();
  } else {
      // Drag End
      if (selectedBuilding) {
          emitObjectUpdate(selectedBuilding);
      }
  }
});

let buildings = [];
let clickableObjects = []; // Stores both buildings and open spaces for raycasting
let selectedBuilding = null;
let hoveredBuilding = null;
let activeSelectionGroup = []; // NEW: Stores all meshes currently highlighted as a group

// --- Semantic Data ---
let masterplanData = {};
let idToPlotMap = {}; // Maps mesh ID -> Plot Key (e.g. "courtyard_58" -> "A1")
let buildingLayerMap = {}; // Maps mesh ID -> Array of Layer Names (e.g. ["Residential", "A1"])

// Layer State
const layerState = {
    Residential: { visible: true, opacity: 1.0, color: 0x4CAF50 },
    A1: { visible: true, opacity: 1.0, color: 0xFF5500 },
    K1: { visible: true, opacity: 1.0, color: 0x00AAFF },
    showDiff: false
};

function initLayerUI() {
    // Check for 'mode=full' before showing layer panel
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('mode') === 'full') {
        document.getElementById('layer-panel').style.display = 'block';
    }

    // Bind Controls
    const bindLayerControl = (key, idPrefix) => {
        const checkbox = document.getElementById(`layer-${idPrefix}`);
        const slider = document.getElementById(`opacity-${idPrefix}`);
        
        if (checkbox && slider) {
            checkbox.addEventListener('change', (e) => {
                layerState[key].visible = e.target.checked;
                applyLayerStyles();
                updateComparisonUI();
            });
            slider.addEventListener('input', (e) => {
                layerState[key].opacity = parseFloat(e.target.value);
                applyLayerStyles();
            });
        }
    };

    bindLayerControl('Residential', 'residential');
    bindLayerControl('A1', 'a1');
    bindLayerControl('K1', 'k1');

    const diffCheckbox = document.getElementById('show-diff');
    if (diffCheckbox) {
        diffCheckbox.addEventListener('change', (e) => {
            layerState.showDiff = e.target.checked;
            applyLayerStyles();
        });
    }
}

function updateComparisonUI() {
    const section = document.getElementById('comparison-section');
    if (layerState.A1.visible && layerState.K1.visible) {
        section.style.display = 'block';
    } else {
        section.style.display = 'none';
        // Auto-turn off diff if one layer is hidden? Maybe not, just hide the controls.
    }
}

function applyLayerStyles() {
    let matchCount = 0;
    console.log('[LayerSystem] Applying Styles...', layerState);
    
    // --- DIAGNOSTIC: TRACER BULLET ---
    const TARGET_ID = "osgb1000041682980"; // A known Residential ID
    const isInMap = buildingLayerMap[TARGET_ID] ? true : false;
    console.log(`[Diagnostic] Target ID (${TARGET_ID}) in Map? ${isInMap}`);
    if (isInMap) {
        console.log(`[Diagnostic] Map Entry:`, buildingLayerMap[TARGET_ID]);
    }

    let targetFoundInScene = false;
    // ---------------------------------

    buildings.forEach(mesh => {
        // Try both, and trim just in case
        const rawFid = mesh.userData.fid;
        const fid = rawFid ? String(rawFid).trim() : null;
        const indexId = String(mesh.userData.id);
        
        // --- DIAGNOSTIC: Check specific ID in scene ---
        if (fid === TARGET_ID) {
            targetFoundInScene = true;
            console.log(`[Diagnostic] Target ID found on Mesh!`);
            console.log(`   - Raw FID: '${rawFid}' (Type: ${typeof rawFid})`);
            console.log(`   - Processed FID: '${fid}'`);
            console.log(`   - Map Lookup:`, buildingLayerMap[fid]);
        }
        // ---------------------------------------------
        
        let layers = [];
        if (fid && buildingLayerMap[fid]) {
            layers = buildingLayerMap[fid];
        } else if (buildingLayerMap[indexId]) {
             layers = buildingLayerMap[indexId];
        }
        
        let finalColor = null;
        let finalOpacity = 1.0;
        let isLayered = false;

        // --- Comparison Logic (High Priority) ---
        if (layerState.showDiff && layerState.A1.visible && layerState.K1.visible) {
            const inA1 = layers.includes('A1');
            const inK1 = layers.includes('K1');

            if (inA1 && inK1) {
                finalColor = 0xAA00AA; // Purple (Both)
                isLayered = true;
            } else if (inA1) {
                finalColor = 0xFF5500; // Orange (Only A1)
                isLayered = true;
            } else if (inK1) {
                finalColor = 0x00AAFF; // Blue (Only K1)
                isLayered = true;
            }
        } 
        
        // --- Standard Layer Logic (if not handled by Comparison) ---
        if (!isLayered) {
            // Priority: K1 > A1 > Residential (Arbitrary visual hierarchy)
            if (layers.includes('K1') && layerState.K1.visible) {
                finalColor = layerState.K1.color;
                finalOpacity = layerState.K1.opacity;
                isLayered = true;
            } else if (layers.includes('A1') && layerState.A1.visible) {
                finalColor = layerState.A1.color;
                finalOpacity = layerState.A1.opacity;
                isLayered = true;
            } else if (layers.includes('Residential') && layerState.Residential.visible) {
                finalColor = layerState.Residential.color;
                finalOpacity = layerState.Residential.opacity;
                isLayered = true;
            }
        }

        // Apply Style
        if (isLayered) {
            matchCount++;
            // Check if we need to create a new material instance to avoid sharing side-effects
            if (!mesh.userData.layerMaterial) {
                mesh.userData.layerMaterial = mesh.userData.originalMaterial.clone();
            }
            
            const mat = mesh.userData.layerMaterial;
            mat.color.setHex(finalColor);
            mat.opacity = finalOpacity;
            
            // ✅ FIX: Always enable transparent for layered materials so opacity sliders work
            mat.transparent = true; 
            
            // ✅ FIX: Mark as needing update to ensure state changes (like transparency) are applied
            // Note: Continuous needsUpdate can be expensive, but necessary if toggling transparent/opaque
            if (mat.opacity < 1.0) {
                 mat.depthWrite = false; // Optional: prevents z-fighting if multiple transparent objects overlap
            } else {
                 mat.depthWrite = true;
            }
            mat.needsUpdate = true;

            // Ensure clipping
            if (globalClippingPlanes.length > 0) {
                 mat.clippingPlanes = globalClippingPlanes;
                 mat.clipShadows = true;
            }
            
            mesh.material = mat;
        } else {
            // Revert to original
            mesh.material = mesh.userData.originalMaterial;
        }
    });

    console.log(`[LayerSystem] Updated ${matchCount} buildings.`);
}

let board;
let streetLampList = [];  // 全局路灯列表
let glowTexture = null;  // 路灯光晕纹理

let buildingMaterial, selectedMaterial, hoveredMaterial;

function initMaterials() {
  // 1. 建筑：纯白石膏质感
  buildingMaterial = new THREE.MeshStandardMaterial({ 
    color: 0xffffff, 
    roughness: 0.6, // 稍微粗糙一点，像石膏
    metalness: 0.0,
    fog: true
  });
  
  selectedMaterial = new THREE.MeshStandardMaterial({ color: 0xff5500, opacity: 0.9, transparent: true, fog: true });
  hoveredMaterial = new THREE.MeshStandardMaterial({ color: 0xffd700, opacity: 0.8, transparent: true, fog: true });
}

// --- Lighting ---
const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
scene.add(ambientLight);

const directionalLight = new THREE.DirectionalLight(0xffffff, 0.8);
directionalLight.castShadow = true;
directionalLight.shadow.radius = 4;
directionalLight.shadow.mapSize.width = 2048;
directionalLight.shadow.mapSize.height = 2048;
scene.add(directionalLight);

// --- Initial Camera Position ---
camera.position.set(500, 500, 500);
controls.target.set(0, 0, 0);
camera.lookAt(0, 0, 0);

// --- Street Lamp System ---
// 创建程序化光晕纹理
function createGlowTexture() {
  if (glowTexture) return glowTexture;
  
  const canvas = document.createElement('canvas');
  canvas.width = 128;
  canvas.height = 128;
  const ctx = canvas.getContext('2d');
  
  // 创建径向渐变
  const gradient = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
  gradient.addColorStop(0, 'rgba(255, 238, 136, 1)');
  gradient.addColorStop(0.2, 'rgba(255, 238, 136, 0.8)');
  gradient.addColorStop(0.5, 'rgba(255, 238, 136, 0.3)');
  gradient.addColorStop(1, 'rgba(255, 238, 136, 0)');
  
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, 128, 128);
  
  glowTexture = new THREE.CanvasTexture(canvas);
  return glowTexture;
}

function createStreetLamp(position) {
  const group = new THREE.Group();

  // --- Pole ---
  const poleGeometry = new THREE.CylinderGeometry(0.1, 0.1, 5, 16);
  const poleMaterial = new THREE.MeshStandardMaterial({ color: 0x333333 });
  const pole = new THREE.Mesh(poleGeometry, poleMaterial);
  pole.position.y = 2.5;
  pole.castShadow = false;  // 性能优化：路灯不产生阴影
  group.add(pole);

  // --- Curved arm ---
  const armGeometry = new THREE.TorusGeometry(0.7, 0.05, 16, 50, Math.PI);
  const armMaterial = new THREE.MeshStandardMaterial({ color: 0x333333 });
  const arm = new THREE.Mesh(armGeometry, armMaterial);
  arm.rotation.z = Math.PI / 2;
  arm.position.set(0, 5, 0);
  arm.castShadow = false;
  group.add(arm);

  // --- Lantern ---
  const lanternGeo = new THREE.SphereGeometry(0.25, 16, 16);
  const lanternMat = new THREE.MeshStandardMaterial({ 
    color: 0xffffcc, 
    emissive: 0xffee88,
    emissiveIntensity: 1 
  });
  const lantern = new THREE.Mesh(lanternGeo, lanternMat);
  lantern.position.set(0.7, 5, 0);
  lantern.castShadow = false;
  group.add(lantern);

  // --- 假光源：用 Sprite 替代 PointLight ---
  const texture = createGlowTexture();
  const spriteMat = new THREE.SpriteMaterial({
    map: texture,
    color: 0xffee88,
    transparent: true,
    opacity: 0.7,
    depthWrite: false,
    blending: THREE.AdditiveBlending  // 叠加混合模式，更亮
  });
  const glow = new THREE.Sprite(spriteMat);
  glow.scale.set(6, 6, 6);  // 🔥 扩大光晕半径（从3到6）
  glow.position.copy(lantern.position);
  group.add(glow);

  // --- Place the lamp on the scene ---
  group.position.copy(position);

  // 为了后期控制白天/夜晚灯光，存入 userData
  group.userData.glow = glow;  // 存储 sprite 而不是 light
  group.userData.lantern = lantern;

  cityGroup.add(group);  // 添加到 cityGroup 以跟随城市旋转

  // 加入全局列表方便管理
  streetLampList.push(group);


  // Sprite 不支持 stencil，但我们可以设置 renderOrder 让它在最后渲染
  // 这样至少它会在其他物体之上，视觉上看起来正确
  glow.renderOrder = 999;

  return group;
}

function addStreetLampsAlongRoad(coords, center) {
  const lampSpacing = 20; // 每20米一个路灯
  const roadOffset = 3; // 路灯距离道路中心线的距离（米）

  let remaining = 0;

  for (let i = 1; i < coords.length; i++) {
    // 转换坐标到场景坐标系（减去中心点偏移）
    const p1 = new THREE.Vector3(coords[i-1][0] - center.x, 0, coords[i-1][1] - center.z);
    const p2 = new THREE.Vector3(coords[i][0] - center.x, 0, coords[i][1] - center.z);

    const segLength = p1.distanceTo(p2);

    // 计算道路方向向量
    const direction = new THREE.Vector3().subVectors(p2, p1).normalize();
    // 计算垂直于道路的偏移向量（左右两侧）
    const perpendicular = new THREE.Vector3(-direction.z, 0, direction.x);

    while (remaining + lampSpacing < segLength) {
      remaining += lampSpacing;

      const t = remaining / segLength;
      const centerPos = new THREE.Vector3().lerpVectors(p1, p2, t);

      // 在道路两侧各放一个路灯
      const leftPos = centerPos.clone().add(perpendicular.clone().multiplyScalar(roadOffset));
      const rightPos = centerPos.clone().add(perpendicular.clone().multiplyScalar(-roadOffset));

      createStreetLamp(leftPos);
      createStreetLamp(rightPos);
    }

    remaining -= segLength;
    if (remaining < 0) remaining = 0;
  }
}

function loadStreetLampsFromRoads(url, center) {
  fetch(url)
    .then(res => res.json())
    .then(geojson => {
      geojson.features.forEach(feature => {
        if (feature.geometry.type === "Polygon" || feature.geometry.type === "MultiPolygon") {
          // roads.geojson 是 Polygon 类型，我们取外环作为道路中心线
          const coords = feature.geometry.type === "Polygon" 
            ? feature.geometry.coordinates[0]
            : feature.geometry.coordinates[0][0];
          
          addStreetLampsAlongRoad(coords, center);
        }
      });
      console.log(`✅ 已生成 ${streetLampList.length} 个路灯`);
    })
    .catch(error => console.error('Error loading street lamps:', error));
}

function updateStreetLamps(sunAltitude) {
  const isNight = sunAltitude < 0;
  const glowOpacity = isNight ? 0.9 : 0.05;  // 夜晚明亮，白天几乎不可见
  const emissive = isNight ? 1 : 0;

  streetLampList.forEach(lamp => {
    lamp.userData.glow.material.opacity = glowOpacity;  // 控制光晕透明度
    lamp.userData.lantern.material.emissiveIntensity = emissive;
  });
}

// 🌙 建筑夜间微发光控制
function updateNightBuildingGlow(altitude) {
  const isNight = altitude < 0;
  const intensity = isNight ? 0.2 : 0.05;  // 夜晚0.2，白天0.05

  buildings.forEach(b => {
    if (b.material && b.material.emissiveIntensity !== undefined) {
      b.material.emissiveIntensity = intensity;
    }
  });
}

// --- Data Loading ---
function loadData() {
  initMaterials();
  
  // Cache-busting timestamp
  const ts = Date.now();

  // Use Promise.all to ensure both Masterplan (Layers) and 3D Geometry are ready
  Promise.all([
    fetch(`/api/masterplan?v=${ts}`).then(res => res.json()),
    fetch(`/api/3d/buildings_3d?v=${ts}`).then(res => res.json())
  ])
  .then(([masterplan, buildingsGeoJSON]) => {
      // 1. Process Masterplan Data
      masterplanData = masterplan;
      const plotKeys = Object.keys(masterplanData);
      console.log("✅ Masterplan Data Loaded. Plots:", plotKeys.join(', '));
      
      if (!masterplanData['Residential']) {
          console.warn("⚠️ WARNING: 'Residential' layer missing from masterplan data! Check file save or cache.");
      }

      for (const [plotKey, plotData] of Object.entries(masterplanData)) {
        if (plotData.ids) {
          plotData.ids.forEach(rawId => {
            const id = String(rawId).trim();
            idToPlotMap[id] = plotKey;
            
            if (!buildingLayerMap[id]) buildingLayerMap[id] = [];
            buildingLayerMap[id].push(plotKey);
          });
        }
      }
      console.log("   Mapped IDs:", Object.keys(buildingLayerMap).length);

      // 2. Build 3D Scene
      const center = buildScene(buildingsGeoJSON);
      console.log("✅ 3D Scene Built. Buildings count:", buildings.length);

      // 3. Initialize UI & Styles (Now safe)
      initLayerUI();
      applyLayerStyles();

      // 4. Load other layers (async, non-blocking)
      const layers = [
        { url: '/api/3d/water', color: 0x44B0C7, type: 'water' },
        { url: '/api/3d/greens', color: 0x4caf50, type: 'greens' },
        { url: '/api/3d/roads', color: 0xCCCCCC, type: 'roads' },
        { url: '/api/3d/paths', color: 0xDDDDDD, type: 'paths' },
        { url: '/api/3d/open_spaces', color: 0xffffff, type: 'open_spaces' }
      ];

      layers.forEach(layer => {
        if (layer.type === 'water') {
          const waterMaterial = new THREE.MeshPhysicalMaterial({
            color: layer.color,
            metalness: 0.1,
            roughness: 0.1,
            transmission: 0.6,
            opacity: 0.9,
            transparent: true,
            side: THREE.DoubleSide
          });
          loadAndDrawLayer(layer.url, waterMaterial, center, 0.1);
        } else if (layer.type === 'open_spaces') {
           loadAndDrawLayer(layer.url, null, center, 0.05);
        } else {
          loadAndDrawLayer(layer.url, layer.color, center, layer.type === 'roads' ? 0.3 : 0.4);
        }
      });
      
      // Check Stencils later
      setTimeout(() => {
        // ... (Keep existing stencil check logic if needed)
      }, 2000);
  })
  .catch(error => {
      console.error('❌ Error during data loading:', error);
      // Fallback: try to load at least buildings if masterplan fails? 
      // For now, fail hard so we see the error.
  });
}

// --- Raycasting ---
const raycaster = new THREE.Raycaster();
const mouse = new THREE.Vector2();

function onMouseMove(event) {
  mouse.x = (event.clientX / window.innerWidth) * 2 - 1;
  mouse.y = -(event.clientY / window.innerHeight) * 2 + 1;
}

let downPos = new THREE.Vector2();

function onMouseDown(event) {
  if (event.button !== 0) return;
  downPos.set(event.clientX, event.clientY);
}

// --- UI Helper: Tooltip ---
function showTooltip(data, x, y) {
  const tooltip = document.getElementById('semantic-tooltip');
  if (!tooltip) return;
  
  document.getElementById('tooltip-title').innerText = data.name || 'Unknown Plot';
  document.getElementById('tooltip-desc').innerText = data.description || 'No description available.';
  
  const tagsContainer = document.getElementById('tooltip-tags');
  tagsContainer.innerHTML = '';
  
  if (data.ai_tags && Array.isArray(data.ai_tags)) {
    data.ai_tags.forEach(tag => {
      const span = document.createElement('span');
      span.className = 'tooltip-tag';
      span.innerText = tag.replace('_', ' ');
      tagsContainer.appendChild(span);
    });
  }
  
  // Position
  // Add offset so it doesn't cover the click
  let left = x + 20;
  let top = y + 20;
  
  // Prevent going off screen
  if (left + 300 > window.innerWidth) left = x - 300;
  if (top + 200 > window.innerHeight) top = y - 200;
  
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
  tooltip.style.display = 'block';
}

function hideTooltip() {
  const tooltip = document.getElementById('semantic-tooltip');
  if (tooltip) tooltip.style.display = 'none';
}

// --- Interaction Helpers ---
function clearSelection() {
  // 1. Restore individual selection
  if (selectedBuilding) {
    selectedBuilding.material = selectedBuilding.userData.originalMaterial;
    selectedBuilding = null;
  }
  
  // 2. Restore group selection
  activeSelectionGroup.forEach(mesh => {
    mesh.material = mesh.userData.originalMaterial;
  });
  activeSelectionGroup = [];
  
  // 3. Hide Tooltips & Panels
  hideTooltip();
  const infoPanel = document.getElementById('info-panel');
  if (infoPanel) infoPanel.style.display = 'none';
  
  transformControls.detach();
}

function updateInfoPanel(mesh) {
    const panel = document.getElementById('info-panel');
    if (!panel) return;

    const id = mesh.userData.fid || mesh.userData.id;
    const height = mesh.userData.currentHeight || mesh.userData.originalHeight || 0;
    
    // Calculate Area (Approximate from Shape)
    let area = 0;
    if (mesh.userData.shapes) {
        mesh.userData.shapes.forEach(shape => {
             area += THREE.ShapeUtils.area(shape.getPoints());
        });
    }
    area = Math.abs(area);

    // Update Text
    const idEl = document.getElementById('info-id');
    if (idEl) idEl.innerText = `ID: ${id}`;
    
    const areaEl = document.getElementById('info-area');
    if (areaEl) areaEl.innerText = `${area.toFixed(0)} m²`;
    
    const heightEl = document.getElementById('info-height');
    if (heightEl) heightEl.innerText = `${height.toFixed(1)} m`;

    // Update Layers
    const layersContainer = document.getElementById('info-layers');
    if (layersContainer) {
        layersContainer.innerHTML = '';
        const layers = buildingLayerMap[id] || [];
        
        if (layers.length === 0) {
            layersContainer.innerHTML = '<span style="color: #999; font-size: 10px; font-style: italic;">No specific layer</span>';
        } else {
            layers.forEach(layer => {
                const tag = document.createElement('span');
                tag.style.cssText = 'background: #ddd; padding: 2px 6px; border-radius: 4px; font-size: 10px; color: #333; margin-right: 4px; margin-bottom: 2px; display: inline-block;';
                tag.innerText = layer;
                
                // Color coding
                if (layer === 'Residential') { tag.style.background = '#E8F5E9'; tag.style.color = '#2E7D32'; }
                if (layer === 'A1') { tag.style.background = '#FFF3E0'; tag.style.color = '#E65100'; }
                if (layer === 'K1') { tag.style.background = '#E1F5FE'; tag.style.color = '#0277BD'; }
                
                layersContainer.appendChild(tag);
            });
        }
    }

    panel.style.display = 'block';
}

function highlightMesh(mesh, colorHex = 0xffff00, opacity = 1.0) {
  // Create a clone of the original material to modify emissive or color
  // For simplicity and performance, we switch to a standard highlighting material
  // that preserves the geometry but makes it glow/pop.
  
  const highlightMat = new THREE.MeshStandardMaterial({
    color: mesh.userData.originalMaterial.color, // Keep original color base
    emissive: colorHex,
    emissiveIntensity: 0.5, // GLOW EFFECT
    transparent: opacity < 1.0,
    opacity: opacity,
    side: THREE.DoubleSide
  });
  
  // If original was textured (e.g. buildings), keep it? 
  // For Phase 1 "Abstract", solid color + glow is better.
  
  if (globalClippingPlanes.length > 0) {
      highlightMat.clippingPlanes = globalClippingPlanes;
      highlightMat.clipShadows = true;
  }
  
  mesh.material = highlightMat;
}

function onMouseUp(event) {
  if (event.button !== 0) return;

  // Check drag
  const upPos = new THREE.Vector2(event.clientX, event.clientY);
  if (downPos.distanceTo(upPos) > 5) return;

  raycaster.setFromCamera(mouse, camera);
  const intersects = raycaster.intersectObjects(clickableObjects);

  // --- DEV TOOL: Shift+Click to gather IDs ---
  if (event.shiftKey && intersects.length > 0) {
    if (!window.RippleDebug) window.RippleDebug = { selectedIds: [] };
    
    const hit = intersects[0].object;
    // Prefer the 'fid' (OSGB ID) if available as it is more unique/persistent than index 'id'
    const id = hit.userData.fid || hit.userData.id;
    
    const idx = window.RippleDebug.selectedIds.indexOf(id);

    if (idx >= 0) {
        // Remove from selection
        window.RippleDebug.selectedIds.splice(idx, 1);
        hit.material = hit.userData.originalMaterial || hit.material;
        console.log(`[Dev] Deselected ${id}`);
    } else {
        // Add to selection
        window.RippleDebug.selectedIds.push(id);
        // Visual indicator: Magenta for dev selection
        highlightMesh(hit, 0xFF00FF, 0.8);
        console.log(`[Dev] Selected ${id}`);
    }

    console.log(`%c Current Selection (${window.RippleDebug.selectedIds.length}):`, 'color: #FF00FF; font-weight: bold;');
    console.log(JSON.stringify(window.RippleDebug.selectedIds));
    return; // Stop normal selection logic
  }

  // Reset previous selection
  clearSelection();

  if (intersects.length > 0) {
    const hit = intersects[0].object;
    const hitID = hit.userData.id;
    
    // ✅ Update Info Panel for ANY click
    updateInfoPanel(hit);
    
    // --- SEMANTIC GROUP SELECTION ---
    const plotKey = idToPlotMap[hitID]; // e.g. "A1"
    
    if (plotKey) {
      console.log(`Hit Group: ${plotKey} (via ${hitID})`);
      const plotInfo = masterplanData[plotKey];
      
      // Find all meshes in this plot
      const groupMeshes = clickableObjects.filter(obj => 
        plotInfo.ids.includes(obj.userData.id)
      );
      
      // Highlight all
      groupMeshes.forEach(mesh => {
        highlightMesh(mesh, 0x00AAFF); // Blue-ish Semantic Glow
        activeSelectionGroup.push(mesh);
      });
      
      // Show UI
      // showTooltip(plotInfo, event.clientX, event.clientY); // Disable Tooltip in favor of Panel? Or keep both?
      // Keeping both for now, but panel is more detailed.
      
    } else {
      // --- FALLBACK: SINGLE SELECTION ---
      console.log("Hit Single:", hitID);
      selectedBuilding = hit;
      
      // Logic for single selection types
      if (hit.userData.type === 'building') {
        transformControls.attach(hit);
        hit.material = selectedMaterial; // Use the old yellow select
      } else if (hit.userData.type === 'water') {
        highlightMesh(hit, 0x00FFFF, 0.5);
      } else if (hit.userData.type === 'open_space') {
        highlightMesh(hit, 0x4CAF50, 0.3);
      }
      
      // Show generic tooltip if needed, or just console
      console.log("Properties:", hit.userData.properties);
    }
  }
}

renderer.domElement.addEventListener('mousemove', onMouseMove, false);
renderer.domElement.addEventListener('mousedown', onMouseDown, false);
renderer.domElement.addEventListener('mouseup', onMouseUp, false);

// --- Render Loop ---
function animate() {
  requestAnimationFrame(animate);

  const delta = clock.getDelta();
  
  if (!isPaused) {
    simTime = new Date(simTime.getTime() + delta * TIME_SPEED * 1000);
    updateTimeLabel();
  }
  
  updateSunFromTime(simTime);

  raycaster.setFromCamera(mouse, camera);
  const intersects = raycaster.intersectObjects(buildings);

  if (hoveredBuilding && hoveredBuilding !== selectedBuilding) {
    hoveredBuilding.material = hoveredBuilding.userData.originalMaterial;
  }
  hoveredBuilding = null;

  if (intersects.length > 0) {
    const newHovered = intersects[0].object;
    if (newHovered !== selectedBuilding) {
      hoveredBuilding = newHovered;
      hoveredBuilding.material = hoveredMaterial;
    }
  }

  controls.update();
  composer.render();
}

function updateSunFromTime(date) {
  const sun = SunCalc.getPosition(date, SITE_LAT, SITE_LON);

  const radius = 1500;
  const altitude = sun.altitude;
  const azimuthScene = sun.azimuth;
  const azimuthWorld = azimuthScene - CITY_ROT_RAD;

  const y = Math.sin(altitude) * radius;
  const flat = Math.cos(altitude) * radius;
  const x = Math.sin(azimuthWorld) * flat;
  const z = Math.cos(azimuthWorld) * flat;

  directionalLight.position.set(x, y, z);
  
  // ✅ 关键：让阴影相机跟随太阳方向
  directionalLight.target.position.set(0, 0, 0);
  directionalLight.target.updateMatrixWorld();
  directionalLight.shadow.camera.updateProjectionMatrix();

  // 🌙 夜景增强：更高的夜间环境光 + 动态天空/雾色
  if (altitude <= 0) {
    ambientLight.intensity = 0.3;  // 🔥 从0.05提升到0.3（月光/天空散射光）
    directionalLight.intensity = 0.0;
    
    // 🌃 夜空背景色和雾色
    renderer.setClearColor(0x0c0c10);  // 深夜蓝黑
    scene.fog.color.set(0x0c0c10);
  } else {
    const k = Math.sin(altitude);
    ambientLight.intensity = 0.3 + 0.3 * k;
    directionalLight.intensity = 0.3 + 0.5 * k;
    
    // ☀️ 白天背景色和雾色
    renderer.setClearColor(0xA3B18A);  // 柔和天空色
    scene.fog.color.set(0xD7D0C8);
  }

  // 🌙 更新路灯状态（夜晚亮灯，白天关灯）
  updateStreetLamps(altitude);
  
  // 🌙 更新建筑夜间微光
  updateNightBuildingGlow(altitude);
}

function updateBuildingGeometry(mesh, newHeight) {
  const shapes = mesh.userData.shapes;

  const extrudeSettings = {
    depth: newHeight,
    bevelEnabled: false
  };

  const newGeom = new THREE.ExtrudeGeometry(shapes, extrudeSettings);
  mesh.geometry.dispose();
  mesh.geometry = newGeom;

  mesh.geometry.computeBoundingBox();
  mesh.geometry.computeBoundingSphere();
  mesh.geometry.computeVertexNormals();

  mesh.position.y = 0;
}

const TILT_DEG   = 40;
const AZIMUTH_DEG = 45;

function setClashCamera(bounds) {
  const size   = bounds.getSize(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.z);

  const center = new THREE.Vector3(0, 0, 0);

  const tilt    = THREE.MathUtils.degToRad(TILT_DEG);
  const azimuth = THREE.MathUtils.degToRad(AZIMUTH_DEG);

  const dist = maxDim;
  const h    = Math.sin(tilt) * dist;
  const r    = Math.cos(tilt) * dist;

  camera.position.set(
    center.x + Math.cos(azimuth) * r,
    center.y + h,
    center.z + Math.sin(azimuth) * r
  );
  camera.lookAt(center);
  controls.target.copy(center);

  const half   = maxDim * 0.5;
  const aspect = window.innerWidth / window.innerHeight;

  camera.left   = -half * aspect;
  camera.right  =  half * aspect;
  camera.top    =  half;
  camera.bottom = -half;

  camera.zoom = 1.5;
  camera.updateProjectionMatrix();
}

// ✅ 这里是关键修改：板子顶面对齐 y=0，颜色改回图一
function createFloatingBoard(width, depth) {
  const shape = new THREE.Shape();
  const w = width / 2;
  const h = depth / 2;

  // Draw sharp rectangle to match clipping planes
  shape.moveTo(-w, -h);
  shape.lineTo(w, -h);
  shape.lineTo(w, h);
  shape.lineTo(-w, h);
  shape.lineTo(-w, -h);

  const extrude = {
    depth: 20,
    bevelEnabled: false, // Disable bevel for clean "architectural model" look
    steps: 1
  };

  const geom = new THREE.ExtrudeGeometry(shape, extrude);
  geom.rotateX(-Math.PI / 2);

  // ✅ 让板子的“顶面”严格落在 y=0 平面上
  geom.computeBoundingBox();
  const box = geom.boundingBox;
  const topY = box.max.y;
  geom.translate(0, -topY, 0);

  const material = new THREE.MeshStandardMaterial({
    color: 0xD2B48C,     // 浅色木纹 (Tan/Light Wood) - 匹配图二
    roughness: 0.6,      // 稍微光滑一点
    metalness: 0.0,
    side: THREE.DoubleSide
  });

  const board = new THREE.Mesh(geom, material);
  board.castShadow = false;
  board.receiveShadow = true;
  board.position.y = 0;        // 顶面在 y=0，和建筑底部齐平

  // === Visual Polish: Add Black Border Line ===
  const borderGeo = new THREE.EdgesGeometry(geom);
  const borderMat = new THREE.LineBasicMaterial({ color: 0x000000, linewidth: 2 });
  const border = new THREE.LineSegments(borderGeo, borderMat);
  border.renderOrder = 1; // Ensure it renders on top
  board.add(border);

  const shadowReceiver = createShadowReceiver(shape, width, depth);
  
  cityGroup.add(shadowReceiver);
  cityGroup.add(board);
  // cityGroup.add(mask); // Removed

  return board;
}

function createShadowReceiver(shape, boardSize) {
  const geom = new THREE.ShapeGeometry(shape);

  const mat = new THREE.ShadowMaterial({
    opacity: 0.5    // ✅ 增强阴影浓度，适应深色底座
  });

  const mesh = new THREE.Mesh(geom, mat);
  mesh.receiveShadow = true;
  mesh.castShadow = false;  // ✅ 关键：不要让阴影接收器自己产生阴影

  mesh.rotation.x = -Math.PI / 2;
  mesh.position.y = 0.01;   // 比板子高一点，避免 Z-fighting

  return mesh;
}

function buildScene(geojson) {
  const features = geojson.features;

  const bounds = new THREE.Box3();
  features.forEach(feature => {
    if (feature.properties.fid === 'osgb1000041681948') return; // 过滤 outlier
    const coords = feature.geometry.coordinates[0];
    coords.forEach(point => {
      bounds.expandByPoint(new THREE.Vector3(point[0], 0, point[1]));
    });
  });
  const center = bounds.getCenter(new THREE.Vector3());
  const size   = bounds.getSize(new THREE.Vector3());

  // Calculate tight bounds for the board
  const boardWidth = size.x * 1.1;
  const boardDepth = size.z * 1.1;
  const maxDim = Math.max(boardWidth, boardDepth);

  boardHalfSize = maxDim / 2;

  // 阴影相机范围
  const SHADOW_SIZE = boardHalfSize * 1.2;

  directionalLight.shadow.camera.left   = -SHADOW_SIZE;
  directionalLight.shadow.camera.right  =  SHADOW_SIZE;
  directionalLight.shadow.camera.top    =  SHADOW_SIZE;
  directionalLight.shadow.camera.bottom = -SHADOW_SIZE;
  directionalLight.shadow.camera.near   = 10;
  directionalLight.shadow.camera.far    = 3000;

  // 阴影质量微调（保留就好）
  directionalLight.shadow.bias        = -0.0001;
  directionalLight.shadow.normalBias  = 0.02;
  directionalLight.shadow.mapSize.set(2048, 2048);

  if (!board) {
    // updateClippingPlanes is now handled in updateSceneVisuals
    // board = createFloatingBoard(boardWidth, boardDepth); 
  }

  features.forEach((feature, index) => {
    // === 修改 1: 过滤掉那个超远的建筑 ===
    if (feature.properties.fid === 'osgb1000041681948') {
      return; // 直接跳过
    }

    const height = feature.properties.height || 10;
    const shapes = [];

    feature.geometry.coordinates.forEach(polygon => {
      const shape = new THREE.Shape();
      polygon.forEach((point, i) => {
        const x = point[0] - center.x;
        const z = point[1] - center.z;
        if (i === 0) shape.moveTo(x, z);
        else shape.lineTo(x, z);
      });
      shapes.push(shape);
    });

    const extrudeSettings = {
      depth: height,
      bevelEnabled: false
    };

    const geometry = new THREE.ExtrudeGeometry(shapes, extrudeSettings);
    const material = buildingMaterial.clone();
    // applyClipping(material); // REMOVED: Handled in updateSceneVisuals
    const mesh = new THREE.Mesh(geometry, material);

    // ✨ 增加描边效果 (Edges)
    const edges = new THREE.EdgesGeometry(geometry, 15); // 15度阈值，只描轮廓
    const line = new THREE.LineSegments(edges, new THREE.LineBasicMaterial({ color: 0x000000, opacity: 0.1, transparent: true }));
    line.raycast = () => {}; // 描边不参与射线检测，优化性能
    mesh.add(line);

    // 超出板子部分不产生阴影
    mesh.material.shadowSide = THREE.FrontSide;

    mesh.castShadow = true;
    mesh.receiveShadow = true;
    mesh.rotation.x = -Math.PI / 2;
    mesh.position.y = 0;
    mesh.userData = {
      id: index,
      fid: String(feature.properties.fid).trim(), // Trim FID for robust matching
      type: 'building', // Explicit type
      properties: feature.properties,
      originalMaterial: material,
      originalHeight: height,
      currentHeight: height,
      shapes: shapes
    };
    buildings.push(mesh);
    clickableObjects.push(mesh); // Add to clickable list
    cityGroup.add(mesh);
  });

  cityGroup.rotation.y = CITY_ROT_RAD;

  setClashCamera(bounds);
  controls.update();

  // 立即计算沙盘和裁剪面，无需等待
  updateSceneVisuals();

  return center;
}

function loadAndDrawLayer(url, colorOrMaterial, center, yOffset = 0) {
  fetch(url)
    .then(res => res.json())
    .then(geojson => {
      console.log(`Loaded ${url}: ${geojson.features.length} features`);

      // --- Special Handling for Open Spaces ---
      if (url.includes('open_spaces')) {
        const material = new THREE.MeshBasicMaterial({
            color: 0xffffff,
            transparent: true,
            opacity: 0.0,    // Invisible base state
            side: THREE.DoubleSide,
            depthWrite: false
        });

        // Apply clipping
        if (globalClippingPlanes.length > 0) {
            material.clippingPlanes = globalClippingPlanes;
            material.clipShadows = true;
        }

        geojson.features.forEach(feature => {
            const shapes = [];
            feature.geometry.coordinates.forEach(polygon => {
                const shape = new THREE.Shape();
                polygon.forEach((point, i) => {
                    const x = point[0] - center.x;
                    const z = point[1] - center.z;
                    if (i === 0) shape.moveTo(x, z);
                    else shape.lineTo(x, z);
                });
                shapes.push(shape);
            });

            const geometry = new THREE.ShapeGeometry(shapes);
            const mesh = new THREE.Mesh(geometry, material);
            mesh.position.y = 0.1; // Slightly raised
            mesh.rotation.x = -Math.PI / 2;
            
            mesh.userData = {
                id: feature.properties.id,
                type: 'open_space',
                properties: feature.properties,
                originalMaterial: material
            };

            scene.add(mesh);
            clickableObjects.push(mesh);
            cityGroup.add(mesh);
        });
        console.log("✅ Open Spaces added to scene & clickable list");
        return; 
      }

      // --- Normal Layer Handling ---
      let material;
      if (colorOrMaterial.isMaterial) {
        material = colorOrMaterial;
      } else {
        material = new THREE.MeshBasicMaterial({
          color: colorOrMaterial,
          side: THREE.DoubleSide
        });
      }

      // ✅ 确保所有层都应用 Clipping (如果已生成)
      if (globalClippingPlanes.length > 0) {
        material.clippingPlanes = globalClippingPlanes;
        material.clipShadows = true;
      }

      geojson.features.forEach((feature, index) => {
        const shapes = [];
        feature.geometry.coordinates.forEach(polygon => {
          const shape = new THREE.Shape();
          polygon.forEach((point, i) => {
            const x = point[0] - center.x;
            const z = point[1] - center.z;
            if (i === 0) shape.moveTo(x, z);
            else shape.lineTo(x, z);
          });
          shapes.push(shape);
        });

        const geometry = new THREE.ShapeGeometry(shapes);
        const mesh = new THREE.Mesh(geometry, material);
        
        mesh.position.y = yOffset;
        mesh.rotation.x = -Math.PI / 2;

        // Add open spaces to clickable list
        if (url.includes('open_spaces')) {
           mesh.userData = {
             id: feature.properties.id,
             type: 'open_space',
             properties: feature.properties,
             originalMaterial: material
           };
           clickableObjects.push(mesh);
        }

        // Add water to clickable list
        if (url.includes('water')) {
           mesh.userData = {
             id: feature.properties.id || `water_${index}`,
             type: 'water',
             properties: feature.properties,
             originalMaterial: material
           };
           clickableObjects.push(mesh);
        }

        cityGroup.add(mesh);
      });
    })
    .catch(error => console.error(`Error loading layer ${url}:`, error));
}

// --- UI / Undo ---
const undoStack = [];
const redoStack = [];

function pushHistory() {
  undoStack.push(captureState());
  redoStack.length = 0;
}

document.getElementById('undo').addEventListener('click', () => {
  if (undoStack.length > 1) {
    redoStack.push(undoStack.pop());
    const prevState = undoStack[undoStack.length - 1];
    restoreState(prevState);
  }
});

document.getElementById('redo').addEventListener('click', () => {
  if (redoStack.length > 0) {
    const nextState = redoStack.pop();
    undoStack.push(nextState);
    restoreState(nextState);
  }
});

document.getElementById('move-button').addEventListener('click', () => transformControls.setMode('translate'));
document.getElementById('scale-button').addEventListener('click', () => transformControls.setMode('scale'));

document.getElementById('height-slider').addEventListener('change', (event) => {
  if (selectedBuilding) {
    pushHistory();
    const newHeight = parseFloat(event.target.value);
    selectedBuilding.userData.currentHeight = newHeight;
    updateBuildingGeometry(selectedBuilding, newHeight);
  }
});

function updateTimeLabel() {
  const y = simTime.getFullYear();
  const m = String(simTime.getMonth() + 1).padStart(2, '0');
  const d = String(simTime.getDate()).padStart(2, '0');
  const hh = String(simTime.getHours()).padStart(2, '0');
  const mm = String(simTime.getMinutes()).padStart(2, '0');
  timeLabel.textContent = `${y}-${m}-${d}  ${hh}:${mm}`;
}

function syncSimTimeFromInputs() {
  const d = dateInput.value || '2024-06-21';
  const t = timeInput.value || '12:00';
  simTime = new Date(`${d}T${t}`);
  updateTimeLabel();
}

function initTimeUI() {
  const y = simTime.getFullYear();
  const m = String(simTime.getMonth() + 1).padStart(2, '0');
  const d = String(simTime.getDate()).padStart(2, '0');
  const hh = String(simTime.getHours()).padStart(2, '0');
  const mm = String(simTime.getMinutes()).padStart(2, '0');

  dateInput.value = `${y}-${m}-${d}`;
  timeInput.value = `${hh}:${mm}`;
  updateTimeLabel();
}

dateInput.addEventListener('change', syncSimTimeFromInputs);
timeInput.addEventListener('change', syncSimTimeFromInputs);

document.getElementById('apply').addEventListener('click', () => {
  console.log('Applying changes:', captureState());
});

let originalState;
let preShowOriginalState;

document.getElementById('show-original').addEventListener('mousedown', () => {
  preShowOriginalState = captureState();
  if (originalState) restoreState(originalState);
});

document.getElementById('show-original').addEventListener('mouseup', () => {
  if (preShowOriginalState) restoreState(preShowOriginalState);
});

function captureState() {
  return buildings.map(b => ({
    id: b.userData.id,
    position: b.position.clone(),
    rotation: new THREE.Euler(0, b.rotation.y, b.rotation.z),
    scale: b.scale.clone(),
    height: b.userData.currentHeight
  }));
}

function restoreState(state) {
  state.forEach(s => {
    const b = buildings.find(x => x.userData.id === s.id);
    if (!b) return;

    b.position.copy(s.position);
    b.rotation.copy(s.rotation);
    b.rotation.x = -Math.PI / 2;
    b.scale.copy(s.scale);

    b.userData.currentHeight = s.height;
    updateBuildingGeometry(b, s.height);
  });
}

// --- Start ---
initTimeUI();
window.addEventListener('resize', () => {
  const aspect = window.innerWidth / window.innerHeight;

  camera.left   = (frustumSize * aspect) / -2;
  camera.right  = (frustumSize * aspect) /  2;
  camera.top    =  frustumSize / 2;
  camera.bottom = -frustumSize / 2;
  camera.updateProjectionMatrix();

  renderer.setSize(window.innerWidth, window.innerHeight);
  composer.setSize(window.innerWidth, window.innerHeight);
  renderTarget.setSize(window.innerWidth, window.innerHeight);
  ssao.setSize(window.innerWidth, window.innerHeight);
});

loadData();
animate();
