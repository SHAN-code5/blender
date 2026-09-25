import sys
sys.path.insert(0, '/Users/shantanubind/ai_3d_generator_extension')
import bpy
import ai_3d_generator as a
print('BL', bpy.app.version)
a.register()
print('REGISTERED', hasattr(bpy.types.Scene, 'ai3d'))
a.unregister()
print('UNREGISTERED', hasattr(bpy.types.Scene, 'ai3d'))
