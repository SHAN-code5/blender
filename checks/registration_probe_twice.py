import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bpy
import ai_3d_generator as a
print('BL', bpy.app.version)
a.register()
a.register()
print('REGISTERED_TWICE', hasattr(bpy.types.Scene, 'ai3d'))
a.unregister()
a.unregister()
print('UNREGISTERED_TWICE', hasattr(bpy.types.Scene, 'ai3d'))
