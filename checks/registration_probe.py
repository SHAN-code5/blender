import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bpy
import ai3dgenerator as a
print('BL', bpy.app.version)
a.register()
print('REGISTERED', hasattr(bpy.types.Scene, 'ai3d'))
a.unregister()
print('UNREGISTERED', hasattr(bpy.types.Scene, 'ai3d'))
