import math, os
from PIL import Image, ImageDraw

OUT = os.path.dirname(os.path.abspath(__file__))
S = 512
RED = (239, 68, 68)      # #ef4444
WHT = (226, 232, 240)    # #e2e8f0
LINE = (71, 85, 105)     # #475569

# 神经节点云（脑形）： (x, y, radius, is_red)
NODES = [
    (256, 120, 22, True),    # 0 顶中
    (150, 150, 19, False),   # 1 顶左
    (362, 150, 19, False),   # 2 顶右
    (100, 226, 20, True),    # 3 左枢纽
    (200, 208, 17, False),   # 4 内上左
    (312, 208, 17, False),   # 5 内上右
    (412, 226, 20, True),    # 6 右枢纽
    (152, 302, 19, False),   # 7 下左
    (256, 292, 27, True),    # 8 核心(最大红)
    (360, 302, 19, False),   # 9 下右
    (256, 374, 17, False),   # 10 茎
]
# 连线（相邻节点成网）
EDGES = [(0,1),(0,2),(0,4),(0,5),(1,3),(1,4),(2,5),(2,6),(3,4),(3,7),(3,8),
         (4,8),(5,8),(5,6),(6,8),(6,9),(7,8),(7,10),(8,9),(8,10),(9,10)]

def build():
    top = (24, 34, 51)     # #182233
    bot = (9, 14, 26)      # #090e1a
    bg = Image.new("RGBA", (S, S))
    b = ImageDraw.Draw(bg)
    for y in range(S):
        t = y / (S - 1)
        col = tuple(int(top[i]*(1-t)+bot[i]*t) for i in range(3))
        b.line([(0, y), (S, y)], fill=col+(255,))
    r = int(S*0.205)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S-1, S-1], radius=r, fill=255)
    img = Image.composite(bg, Image.new("RGBA", (S, S), (0, 0, 0, 0)), mask)
    d = ImageDraw.Draw(img)
    cx, cy = 256, 250

    # 中心柔光
    for i in range(150, 0, -2):
        d.ellipse([cx-i, cy-i, cx+i, cy+i], fill=RED+(int(26*(1-i/150)),))

    # 连线
    for a, bb in EDGES:
        x1, y1, _, _ = NODES[a]
        x2, y2, _, _ = NODES[bb]
        d.line([(x1, y1), (x2, y2)], fill=LINE+(170,), width=5)

    # 节点
    for (x, y, R, is_red) in NODES:
        base = RED if is_red else WHT
        for i in range(9, 0, -1):  # 光晕
            d.ellipse([x-R-i, y-R-i, x+R+i, y+R+i], fill=base+(20,))
        d.ellipse([x-R, y-R, x+R, y+R], fill=base+(255,))
        d.ellipse([x-R*0.55, y-R*0.62, x-R*0.05, y-R*0.18], fill=(255, 255, 255, 130))

    # 核心再强调
    Rc = 30
    d.ellipse([cx-Rc, cy-Rc, cx+Rc, cy+Rc], fill=RED+(255,))
    d.ellipse([cx-Rc*0.55, cy-Rc*0.6, cx-Rc*0.05, cy-Rc*0.15], fill=(255, 255, 255, 160))
    return img

img = build()
img.save(os.path.join(OUT, "_icon_preview.png"))
img.save(os.path.join(OUT, "cordis.ico"),
         sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("ICON_SAVED preview + cordis.ico")
