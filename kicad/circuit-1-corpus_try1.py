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

# Input terminal
seg(10, 30, 20, 30, 0.4)  # input line
text(5, 28, "IN", 2.5)

# Resistor R1
seg(20, 25, 30, 25, 0.4)  # horizontal resistor line
seg(20, 25, 20, 35, 0.4)  # vertical left leg
seg(30, 25, 30, 35, 0.4)  # vertical right leg
text(22, 20, "R1", 2.5)

# Capacitor C1
seg(30, 30, 40, 30, 0.4)  # horizontal capacitor line
seg(35, 25, 35, 35, 0.4)  # vertical plate
seg(40, 25, 40, 35, 0.4)  # vertical plate
text(32, 35, "C1", 2.5)

# Output terminal
seg(40, 30, 50, 30, 0.4)  # output line
text(52, 28, "OUT", 2.5)

# Ground symbol
seg(40, 30, 40, 35, 0.4)
seg(38, 35, 42, 35, 0.4)
seg(39, 36, 41, 36, 0.4)

pcbnew.SaveBoard(r'C:\Users\kevin\ai_bench\image-models-20261020\kicad\circuit-1-corpus.kicad_pcb', board)
