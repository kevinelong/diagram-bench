import pcbnew
MM = 1_000_000
board = pcbnew.CreateEmptyBoard()
def seg(x1, y1, x2, y2, w=0.4):
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_SEGMENT)
    s.SetLayer(pcbnew.F_SilkS)
    s.SetStart(pcbnew.VECTOR2I(int(x1*MM), int(y1*MM)))
    s.SetEnd(pcbnew.VECTOR2I(int(x2*MM), int(y2*MM)))
    s.SetWidth(int(w*MM))
    board.Add(s)
def circ(cx, cy, r, filled=False, lw=0.3):
    c = pcbnew.PCB_SHAPE(board)
    c.SetShape(pcbnew.SHAPE_T_CIRCLE)
    c.SetLayer(pcbnew.F_SilkS)
    c.SetCenter(pcbnew.VECTOR2I(int(cx*MM), int(cy*MM)))
    c.SetEnd(pcbnew.VECTOR2I(int((cx+r)*MM), int(cy*MM)))
    c.SetWidth(int(lw*MM))
    c.SetFilled(filled)
    board.Add(c)
def rect(x, y, w, h, lw=0.4):
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_RECT)
    s.SetLayer(pcbnew.F_SilkS)
    s.SetStart(pcbnew.VECTOR2I(int(x*MM), int(y*MM)))
    s.SetEnd(pcbnew.VECTOR2I(int((x+w)*MM), int((y+h)*MM)))
    s.SetWidth(int(lw*MM))
    board.Add(s)
def text(x, y, value, size=2.5):
    tx = pcbnew.PCB_TEXT(board)
    tx.SetText(value)
    tx.SetLayer(pcbnew.F_SilkS)
    tx.SetPosition(pcbnew.VECTOR2I(int(x*MM), int(y*MM)))
    tx.SetTextSize(pcbnew.VECTOR2I(int(size*MM), int(size*MM)))
    tx.SetTextThickness(int(0.3*MM))
    board.Add(tx)

# Draw dark blue background rectangle
rect(0, 0, 90, 60, lw=0.1)

# Draw bracket outline (rectangle)
rect(10, 15, 70, 30)

# Draw two mounting holes
circ(20, 30, 5)  # hole 1
circ(70, 30, 5)  # hole 2

# Draw dimension lines with arrows
# Horizontal dimension line from left to right
seg(10, 45, 80, 45, w=0.3)
# Arrows on horizontal line
seg(10, 43, 10, 47, w=0.3)
seg(80, 43, 80, 47, w=0.3)

# Vertical dimension line from top to bottom
seg(5, 15, 5, 45, w=0.3)
# Arrows on vertical line
seg(3, 15, 7, 15, w=0.3)
seg(3, 45, 7, 45, w=0.3)

# Add text labels A and B
text(5, 50, "A", size=2.5)
text(85, 20, "B", size=2.5)

pcbnew.SaveBoard(r'C:\Users\kevin\ai_bench\image-models-20261020\kicad\blueprint-1-corpus.kicad_pcb', board)
