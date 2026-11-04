"""Draw floor_plan.dxf over source_sketch.jpg to check alignment.

Usage: python check_overlay.py source_overlay.png  (also writes *_gf.png, *_ff.png)
Red = walls, orange = glass, green = doors, magenta = stairs, blue dots = labels.
"""
import sys, ezdxf
from ezdxf import path as ezpath
from PIL import Image, ImageDraw
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
import build_floor_plan_dxf as plan
K = 4080/2000
im = Image.open('source_sketch.jpg').convert('RGB')
im = Image.blend(im, Image.new('RGB', im.size, 'white'), 0.35)
d = ImageDraw.Draw(im)
def topx(x, y):
    if x >= plan.FF_DX - 1:
        x -= plan.FF_DX; return ((1125.8 + 46.18*x)*K, (879.9 - 45.36*y)*K)
    return ((365 + 48*x)*K, (905 - 48*y)*K)
col = {'A-WALL':(255,0,0),'A-WALL-OUTER':(255,0,0),'A-GLAZ':(255,140,0),'A-DOOR':(0,170,0),'A-STAIR':(200,0,200)}
doc = ezdxf.readfile('floor_plan.dxf')
for e in doc.modelspace():
    c = col.get(e.dxf.layer)
    if not c or e.dxftype() not in ('LINE','ARC','LWPOLYLINE'): continue
    p = ezpath.make_path(e)
    pts = [topx(v.x, v.y) for v in p.flattening(0.01)]
    if e.dxftype()=='LWPOLYLINE' and e.closed: pts.append(pts[0])
    d.line(pts, fill=c, width=3)
for e in doc.modelspace().query('TEXT'):
    x,y = topx(*e.dxf.align_point.vec2)
    d.ellipse([x-4,y-4,x+4,y+4], fill=(0,150,255))
im.save(sys.argv[1])
gf = im.crop((int(300*K),0,int(880*K),int(924*K))); gf.save(sys.argv[1].replace('.png','_gf.png'))
ff = im.crop((int(1090*K),int(170*K),int(1610*K),int(900*K))); ff.save(sys.argv[1].replace('.png','_ff.png'))
