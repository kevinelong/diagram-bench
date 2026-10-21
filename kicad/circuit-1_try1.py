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

# Input terminal
seg(5, 30, 15, 30)  # horizontal line

# Resistor R1
seg(15, 25, 25, 35)
seg(15, 35, 25, 25)
seg(25, 25, 35, 35)
seg(25, 35, 35, 25)
seg(35, 25, 45, 35)
seg(35, 35, 45, 25)
seg(45, 25, 55, 35)
seg(45, 35, 55, 25)
seg(55, 25, 65, 35)
seg(55, 35, 65, 25)
seg(65, 25, 75, 35)
seg(65, 35, 75, 25)
seg(75, 25, 85, 35)
seg(75, 35, 85, 25)

# Capacitor C1
seg(85, 30, 95, 30)  # horizontal line
seg(95, 25, 95, 35)  # vertical line

# Output terminal
seg(95, 30, 105, 30)  # horizontal line

# Labels
text(40, 20, "R1")
text(90, 20, "C1")

pcbnew.SaveBoard(r'C:\Users\kevin\ai_bench\image-models-20261020\kicad\circuit-1.kicad_pcb', board)
