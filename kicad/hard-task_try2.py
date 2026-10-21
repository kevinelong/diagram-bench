import pcbnew
import math

def main():
    # Create a new board
    board = pcbnew.LoadBoard(None)
    
    # Define table dimensions (2:1 ratio, 100mm x 50mm in mm)
    table_width = 100000  # 100mm in μm
    table_height = 50000  # 50mm in μm
    table_start = pcbnew.VECTOR2I(0, 0)
    table_end = pcbnew.VECTOR2I(table_width, table_height)
    
    # Add table rectangle
    add_rectangle(board, pcbnew.F_SilkS, table_start, table_end, 10000)
    
    # Add pockets (circles with 5mm radius)
    pocket_radius = 5000  # 5mm in μm
    
    # Corner pockets
    pockets = [
        (0, 0),                    # top-left
        (table_width, 0),          # top-right
        (0, table_height),         # bottom-left
        (table_width, table_height) # bottom-right
    ]
    
    # Side pockets (middle of long sides)
    pockets.append((0, table_height // 2))      # middle-left
    pockets.append((table_width, table_height // 2))  # middle-right
    
    for x, y in pockets:
        add_circle(board, pcbnew.F_SilkS, pcbnew.VECTOR2I(x, y), pocket_radius, 10000)
    
    # Add object balls in triangle rack (right side)
    # Rack positions: rows of 1, 2, 3, 4, 5
    ball_radius = 3000  # 3mm radius in μm
    spacing = 6000  # 6mm spacing between centers
    
    # Starting position near right end (table_width - 20mm from right edge)
    rack_start_x = table_width - 20000  # 20mm from right edge
    rack_start_y = table_height // 2  # center vertically
    
    # Add balls in triangle pattern
    row_positions = [0, -spacing, -2*spacing, -3*spacing, -4*spacing]
    
    for i, y_offset in enumerate(row_positions):
        row_length = i + 1
        start_x = rack_start_x - (row_length - 1) * spacing // 2
        
        for j in range(row_length):
            x_pos = start_x + j * spacing
            y_pos = rack_start_y + y_offset
            add_circle(board, pcbnew.F_SilkS, pcbnew.VECTOR2I(x_pos, y_pos), ball_radius, 10000)
    
    # Add cue ball (left of rack)
    cue_ball_x = rack_start_x - 15000  # 15mm left of rack
    cue_ball_y = rack_start_y
    add_circle(board, pcbnew.F_SilkS, pcbnew.VECTOR2I(cue_ball_x, cue_ball_y), ball_radius, 10000)
    
    # Add cue stick (long thin rectangle)
    # Cue stick starts at left edge and angles toward cue ball
    stick_width = 500  # 0.5mm wide
    stick_length = 30000  # 30mm long
    
    # Calculate angle to cue ball
    dx = cue_ball_x - 0
    dy = cue_ball_y - (table_height // 2)
    angle = math.atan2(dy, dx)
    
    # Create stick rectangle (centered at left edge)
    stick_start = pcbnew.VECTOR2I(0, table_height // 2)
    stick_end = pcbnew.VECTOR2I(stick_length, 0)
    
    # Add cue stick as a rectangle
    add_rectangle(board, pcbnew.F_SilkS, stick_start, stick_end, stick_width)
    
    # Add text label "CUE" for cue ball
    cue_text = pcbnew.PCB_TEXT(board)
    cue_text.SetText("CUE")
    cue_text.SetLayer(pcbnew.F_SilkS)
    cue_text.SetPosition(pcbnew.VECTOR2I(cue_ball_x, cue_ball_y - 5000))  # Above the cue ball
    cue_text.SetHorizJustify(pcbnew.GR_TEXT_HJUSTIFY_CENTER)
    cue_text.SetVertJustify(pcbnew.GR_TEXT_VJUSTIFY_CENTER)
    cue_text.SetThickness(1000)  # 1mm thickness
    cue_text.SetSize(pcbnew.VECTOR2I(1500, 1500))  # 1.5mm text size
    
    board.Add(cue_text)
    
    # Save the board
    pcbnew.SaveBoard(r'C:\Users\kevin\ai_bench\image-models-20261020\kicad\hard-task-bare.kicad_pcb', board)

def add_rectangle(board, layer, start, end, width):
    # Create a rectangle from two points
    rect = pcbnew.PCB_SHAPE(board)
    rect.SetShape(pcbnew.SHAPE_T_RECTANGLE)
    rect.SetLayer(layer)
    rect.SetStart(start)
    rect.SetEnd(end)
    rect.SetWidth(width)
    board.Add(rect)

def add_circle(board, layer, center, radius, width):
    # Create a circle
    circle = pcbnew.PCB_SHAPE(board)
    circle.SetShape(pcbnew.SHAPE_T_CIRCLE)
    circle.SetLayer(layer)
    circle.SetCenter(center)
    circle.SetRadius(radius)
    circle.SetWidth(width)
    board.Add(circle)

if __name__ == "__main__":
    main()
