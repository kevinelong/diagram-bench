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
rect(0, 0, 90, 60, lw=0.4)

# Draw bracket rectangle (white)
rect(10, 15, 70, 30, lw=0.4)

# Draw two circular mounting holes (white)
seg(20, 25, 20, 35, w=0.4)  # hole 1 vertical line
seg(70, 25, 70, 35, w=0.4)  # hole 2 vertical line

# Draw dimension lines with arrows
# Horizontal dimension line A
seg(10, 10, 80, 10, w=0.4)
seg(10, 10, 10, 12, w=0.4)  # arrow
seg(80, 10, 80, 12, w=0.4)  # arrow

# Vertical dimension line B
seg(5, 15, 5, 45, w=0.4)
seg(5, 15, 3, 15, w=0.4)  # arrow
seg(5, 45, 3, 45, w=0.4)  # arrow

# Add text labels
text(40, 5, "A", size=2.5)
text(5, 30, "B", size=2.5)

pcbnew.SaveBoard(r'C:\Users\kevin\ai_bench\image-models-20261020\kicad\blueprint-1.kicad_pcb', board)
