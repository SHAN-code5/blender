"""Deterministic grass cinematography, Blender 4.5 LTS. No external assets."""
import argparse
import math
import random
import sys
from pathlib import Path

import bpy
from mathutils import Vector

P = argparse.ArgumentParser()
P.add_argument('--shot', type=int, default=0)
P.add_argument('--output', type=Path, required=True)
P.add_argument('--still', action='store_true')
P.add_argument('--engine', default='CYCLES')
P.add_argument('--samples', type=int, default=6)
args = P.parse_args(sys.argv[sys.argv.index('--') + 1:])
random.seed(730 + args.shot)
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
S = bpy.context.scene
S.render.engine = args.engine
S.render.resolution_x = 1920
S.render.resolution_y = 1080
S.render.resolution_percentage = 100
S.render.fps = 4
S.frame_start = 1
S.frame_end = 44
S.render.threads_mode = 'FIXED'
S.render.threads = 8
S.render.image_settings.file_format = 'PNG'
S.render.image_settings.color_mode = 'RGB'
S.render.image_settings.compression = 12
if args.engine == 'CYCLES':
    S.cycles.samples = args.samples
    S.cycles.use_denoising = True
    S.cycles.max_bounces = 4
    S.cycles.transmission_bounces = 3
    S.cycles.use_persistent_data = True
else:
    S.eevee.taa_render_samples = args.samples
S.view_settings.view_transform = 'AgX'
S.view_settings.exposure = -.35

def material(name, color, roughness=.48, metallic=0):
    m = bpy.data.materials.new(name)
    m.diffuse_color = (*color, 1)
    m.use_nodes = True
    p = m.node_tree.nodes.get('Principled BSDF')
    p.inputs['Base Color'].default_value = (*color, 1)
    p.inputs['Roughness'].default_value = roughness
    p.inputs['Metallic'].default_value = metallic
    return m

def grass_material(name, dry=False):
    m = material(name, (.12, .23, .026))
    n = m.node_tree.nodes
    l = m.node_tree.links
    p = n.get('Principled BSDF')
    p.inputs['Subsurface Weight'].default_value = .08
    p.inputs['Subsurface Radius'].default_value = (.4, 1, .15)
    p.inputs['Coat Weight'].default_value = .18
    tex = n.new('ShaderNodeTexNoise')
    tex.inputs['Scale'].default_value = 7
    tex.inputs['Detail'].default_value = 3
    ramp = n.new('ShaderNodeValToRGB')
    ramp.color_ramp.elements[0].position = .18
    ramp.color_ramp.elements[0].color = (.025, .055, .008, 1) if not dry else (.12, .063, .014, 1)
    ramp.color_ramp.elements[1].color = (.30, .48, .06, 1) if not dry else (.62, .42, .12, 1)
    l.new(tex.outputs['Fac'], ramp.inputs[0])
    l.new(ramp.outputs['Color'], p.inputs['Base Color'])
    trans = n.new('ShaderNodeBsdfTranslucent')
    l.new(ramp.outputs[0], trans.inputs[0])
    mix = n.new('ShaderNodeMixShader')
    mix.inputs[0].default_value = .12
    l.new(p.outputs[0], mix.inputs[1])
    l.new(trans.outputs[0], mix.inputs[2])
    l.new(mix.outputs[0], n.get('Material Output').inputs[0])
    return m

green = grass_material('Living chlorophyll • wax and transmitted light')
straw = grass_material('Ripe grass • ochre veins', True)
soil = material('Mossy meadow floor', (.035, .055, .012))
seedmat = material('Fine golden grass florets', (.36, .23, .065), .66)
water = material('Dew • water', (.84, .94, 1), .035)
wp = water.node_tree.nodes.get('Principled BSDF')
wp.inputs['Transmission Weight'].default_value = 1
wp.inputs['IOR'].default_value = 1.333

def mesh_obj(name, vertices, faces, mat):
    me = bpy.data.meshes.new(name)
    me.from_pydata(vertices, [], faces)
    me.update()
    ob = bpy.data.objects.new(name, me)
    S.collection.objects.link(ob)
    me.materials.append(mat)
    for p in me.polygons:
        p.use_smooth = True
    return ob

def blade(name, h, width, bend, mat, tuft=1):
    vertices, faces = [], []
    for j in range(10):
        t = j / 9
        w = width * (math.sin(math.pi * (t * .90 + .08))) * (1 - t * .96)
        x = bend * t * t
        vertices.extend([(x-w, 0, h*t), (x, -.014*math.sin(t*math.pi), h*t+.004), (x+w, 0, h*t)])
        if j:
            a = (j-1)*3
            faces.extend([(a,a+3,a+4,a+1), (a+1,a+4,a+5,a+2)])
    if tuft>1:
        baseverts=list(vertices);basefaces=list(faces)
        for k in range(1,tuft):
            angle=random.uniform(0,math.tau);scale=random.uniform(.55,1.15)
            dx,dy=random.uniform(-.075,.075),random.uniform(-.075,.075)
            offset=len(vertices)
            vertices.extend([(x*math.cos(angle)-y*math.sin(angle)+dx,x*math.sin(angle)+y*math.cos(angle)+dy,z*scale) for x,y,z in baseverts])
            faces.extend([tuple(a+offset for a in f) for f in basefaces])
    ob = mesh_obj(name, vertices, faces, mat)
    ob.shape_key_add(name='Rest')
    key = ob.shape_key_add(name='Breeze')
    for v in key.data:
        t = v.co.z/h
        v.co.x += .18*t*t
        v.co.y += .06*t*t
    for f,v in [(1,.1),(34,1),(72,.05),(105,.8),(132,.1)]:
        key.value=v
        key.keyframe_insert('value',frame=f)
    return ob

protos = bpy.data.collections.new('Grass blade palette')
S.collection.children.link(protos)
for i in range(7):
    ob = blade('Blade variety %d'%i, random.uniform(.7,1.4), random.uniform(.022,.045), random.uniform(-.35,.35), straw if args.shot>=6 and i%3 else green, tuft=7)
    for col in list(ob.users_collection):
        col.objects.unlink(ob)
    protos.objects.link(ob)
    ob.location=(0,0,-30)

# Geometry Nodes scatter actual tapered, ridged blade meshes, not hair cards.
verts, faces = [], []
N=60
for y in range(N+1):
    for x in range(N+1):
        xx=(x/N-.5)*48
        yy=(y/N-.5)*48
        verts.append((xx,yy,.13*math.sin(xx*.23)*math.sin(yy*.28)))
for y in range(N):
    for x in range(N):
        a=y*(N+1)+x
        faces.append((a,a+1,a+N+2,a+N+1))
ground=mesh_obj('Rolling meadow',verts,faces,soil)
scatter=ground.modifiers.new('Procedural meadow • 45k blades','NODES')
g=bpy.data.node_groups.new('Grass field / seeded distribution','GeometryNodeTree')
g.interface.new_socket(name='Geometry',in_out='INPUT',socket_type='NodeSocketGeometry')
g.interface.new_socket(name='Geometry',in_out='OUTPUT',socket_type='NodeSocketGeometry')
scatter.node_group=g
n=g.nodes; l=g.links
inp=n.new('NodeGroupInput'); out=n.new('NodeGroupOutput')
dist=n.new('GeometryNodeDistributePointsOnFaces'); dist.inputs['Density'].default_value=9
dist.inputs['Seed'].default_value=42
l.new(inp.outputs[0],dist.inputs['Mesh'])
ci=n.new('GeometryNodeCollectionInfo');ci.inputs['Collection'].default_value=protos
ci.inputs['Separate Children'].default_value=True
ci.inputs['Reset Children'].default_value=True
inst=n.new('GeometryNodeInstanceOnPoints'); inst.inputs['Pick Instance'].default_value=True
l.new(dist.outputs['Points'],inst.inputs['Points']);l.new(ci.outputs['Instances'],inst.inputs['Instance'])
r=n.new('FunctionNodeRandomValue');r.data_type='FLOAT_VECTOR'
r.inputs['Min'].default_value=(.65,.65,.45);r.inputs['Max'].default_value=(1.5,1.5,1.45)
l.new(r.outputs['Value'],inst.inputs['Scale'])
rot=n.new('FunctionNodeRandomValue');rot.data_type='FLOAT_VECTOR'
rot.inputs['Min'].default_value=(-.15,-.15,0);rot.inputs['Max'].default_value=(.15,.15,6.283)
l.new(rot.outputs['Value'],inst.inputs['Rotation'])
join=n.new('GeometryNodeJoinGeometry'); l.new(inp.outputs[0],join.inputs[0]);l.new(inst.outputs[0],join.inputs[0]);l.new(join.outputs[0],out.inputs[0])

def cylinder_between(name,a,b,r,mat,vertices=7):
    d=Vector(b)-Vector(a)
    bpy.ops.mesh.primitive_cone_add(vertices=vertices,radius1=r,radius2=r*.65,depth=d.length,location=(Vector(a)+Vector(b))/2)
    ob=bpy.context.object;ob.name=name;ob.rotation_euler=d.to_track_quat('Z','Y').to_euler();ob.data.materials.append(mat)
    return ob

def seed_head(x,y,h,hero=False):
    root=bpy.data.objects.new('Wind-bent grass inflorescence',None);S.collection.objects.link(root);root.location=(x,y,0)
    stem=cylinder_between('Fine flowering culm',(0,0,0),(.12,0,h),.009,green);stem.parent=root
    # Alternating spikelets and fine awns form recognizable grass seed heads.
    for j in range(13):
        z=h-.35+j*.026
        side=(-1)**j
        xx=.12+side*(.045+.022*math.sin(j*.24))
        bpy.ops.mesh.primitive_uv_sphere_add(segments=12,ring_count=6,radius=1,location=(xx,0,z))
        grain=bpy.context.object;grain.name='Grass spikelet';grain.scale=(.022,.016,.048);grain.rotation_euler[1]=side*.48;grain.data.materials.append(seedmat);grain.parent=root
        for poly in grain.data.polygons:poly.use_smooth=True
        awn=cylinder_between('Silken awn',(xx,0,z+.025),(xx+side*.065,.01,z+.14),.0012,seedmat,4);awn.parent=root
    for f,a in [(1,-.045),(40,.08),(84,-.065),(132,.02)]:
        root.rotation_euler=(a*.3,a,0);root.keyframe_insert('rotation_euler',frame=f)
    return root

for i in range(35):
    seed_head(random.uniform(-13,13),random.uniform(-3,18),random.uniform(1.1,2.15))

hero=blade('Hero blade',1.45,.065,.40,green)
hero.location=(0,0,0)
if args.shot==2:
    for loc,rad in [((.25,-.026,1.04),.073),((.31,-.019,1.18),.031),((.17,-.03,.89),.024)]:
        bpy.ops.mesh.primitive_uv_sphere_add(segments=40,ring_count=20,radius=rad,location=loc)
        drop=bpy.context.object;drop.name='Surface tension • dewdrop';drop.data.materials.append(water)
        for p in drop.data.polygons:p.use_smooth=True
    # Dew is a still, sheltered moment.
    hero.data.shape_keys.animation_data_clear()
if args.shot in (3,6):
    for x,y,h in [(-.45,0,1.7),(.2,.1,1.9),(.65,.6,1.6)]:seed_head(x,y,h,True)

world=bpy.data.worlds.new('Open sky');S.world=world;world.use_nodes=True
bg=world.node_tree.nodes.get('Background')
sky=world.node_tree.nodes.new('ShaderNodeTexSky');sky.sky_type='NISHITA'
sky.sun_elevation=math.radians([5,14,24,37,55,32,7,2,-5][args.shot]);sky.sun_rotation=math.radians(140)
sky.air_density=1.3;sky.dust_density=2
world.node_tree.links.new(sky.outputs[0],bg.inputs[0]);bg.inputs[1].default_value=.35 if args.shot!=8 else .035
bpy.ops.object.light_add(type='SUN',location=(-4,6,10));sun=bpy.context.object
sun.rotation_euler=(math.radians(70),math.radians(-22),math.radians(-145));sun.data.energy=[1.2,1.8,2.0,2.3,1.4,.7,2.1,1.0,.12][args.shot];sun.data.angle=.12
sun.data.color=(1,.77,.45) if args.shot in (0,6,7) else (.86,.93,1)
bpy.ops.object.light_add(type='AREA',location=(0,-2,6));bpy.context.object.data.energy=120;bpy.context.object.data.shape='DISK';bpy.context.object.data.size=8

if args.shot==5:
    rainmat=material('Rain glints',(.55,.70,.8),.12)
    for i in range(220):
        x,y,z=random.uniform(-4,4),random.uniform(-3,6),random.uniform(.3,5)
        ob=cylinder_between('Falling rain',(x,y,z),(x-.018,y,z+.14),.0018,rainmat,4)
        ob.keyframe_insert('location',frame=1);ob.location.z-=4;ob.location.x+=.4;ob.keyframe_insert('location',frame=132)
if args.shot==8:
    glow=material('Firefly lantern',(.5,1,.06))
    gp=glow.node_tree.nodes.get('Principled BSDF');gp.inputs['Emission Color'].default_value=(.5,1,.05,1);gp.inputs['Emission Strength'].default_value=7
    for i in range(55):
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1,radius=random.uniform(.006,.017),location=(random.uniform(-7,7),random.uniform(-1,13),random.uniform(.4,2.5)))
        ob=bpy.context.object;ob.name='Firefly';ob.data.materials.append(glow)
        ob.keyframe_insert('location',frame=1);ob.location+=Vector((random.uniform(-.5,.5),0,random.uniform(-.3,.3)));ob.keyframe_insert('location',frame=132)

# (camera, focus, lens, f-stop): alternate spacious field and intimate macro.
shots=[((7,-14,5.0),(0,4,.7),43,5.6),((3,-8,2.6),(0,3,1.1),53,2.8),((.58,-1.15,1.18),(.25,0,1.04),65,2.0),((1.7,-3.8,1.95),(0,0,1.55),65,1.8),((-4,-8,1.1),(0,2,.85),46,2.8),((.2,-3.8,1.0),(0,0,.9),62,2.0),((1.2,-4,1.9),(.1,.1,1.65),65,1.8),((8,-15,3.4),(0,6,1),42,4),((3,-8,1.6),(0,3,1.2),48,2.0)]
pos,target,lens,fstop=shots[args.shot]
bpy.ops.object.empty_add(location=target);focus=bpy.context.object;focus.name='Focus plane'
bpy.ops.object.camera_add(location=pos);cam=bpy.context.object;S.camera=cam
cam.data.lens=lens;cam.data.clip_end=250;cam.data.dof.use_dof=True;cam.data.dof.focus_object=focus;cam.data.dof.aperture_fstop=fstop;cam.data.dof.aperture_blades=7
for frame,delta in [(1,-.12),(132,.12)]:
    cam.location=Vector(pos)+Vector((delta,delta*.2,delta*.08))
    cam.rotation_euler=(Vector(target)-cam.location).to_track_quat('-Z','Y').to_euler()
    cam.keyframe_insert('location',frame=frame);cam.keyframe_insert('rotation_euler',frame=frame)
for fc in cam.animation_data.action.fcurves:
    for kp in fc.keyframe_points:kp.interpolation='LINEAR'

# Retain the eleven-second choreography at the economical source cadence.
for action in bpy.data.actions:
    for curve in action.fcurves:
        for key in curve.keyframe_points:
            key.co.x = 1 + (key.co.x-1)*43/131
            key.handle_left.x = key.co.x
            key.handle_right.x = key.co.x

S.use_nodes=True;nodes=S.node_tree.nodes;nodes.clear();links=S.node_tree.links
rl=nodes.new('CompositorNodeRLayers');comp=nodes.new('CompositorNodeComposite')
S.view_layers[0].use_pass_z=True
fog=nodes.new('CompositorNodeMapRange');fog.inputs['From Min'].default_value=8;fog.inputs['From Max'].default_value=65 if args.shot!=0 else 40;fog.inputs['To Max'].default_value=.8 if args.shot==0 else .40;fog.use_clamp=True
links.new(rl.outputs['Depth'],fog.inputs[0])
mix=nodes.new('CompositorNodeMixRGB');mix.inputs[2].default_value=(.38,.46,.40,1) if args.shot==0 else ((.38,.49,.62,1) if args.shot<6 else ((.5,.25,.09,1) if args.shot<8 else (.008,.015,.035,1)))
links.new(fog.outputs[0],mix.inputs[0]);links.new(rl.outputs['Image'],mix.inputs[1])
glare=nodes.new('CompositorNodeGlare');glare.glare_type='FOG_GLOW';glare.quality='MEDIUM';glare.threshold=1.5
links.new(mix.outputs[0],glare.inputs[0]);links.new(glare.outputs[0],comp.inputs[0])
args.output.mkdir(parents=True,exist_ok=True)
if args.still:
    S.frame_set(22);S.render.filepath=str(args.output/f'shot_{args.shot+1:02d}.png');bpy.ops.render.render(write_still=True)
else:
    S.render.filepath=str(args.output/'frame_');bpy.ops.render.render(animation=True)
