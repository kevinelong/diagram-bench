import pcbnew
import math

def add_rectangle(board, layer, start, end, width):
    """Add a rectangle to the board"""
    rect = pcbnew.PCB_SHAPE(board)
    rect.SetShape(pcbnew.SHAPE_T_RECTANGLE)
    rect.SetStart(start)
    rect.SetEnd(end)
    rect.SetWidth(width)
    rect.SetLayer(layer)
    board.Add(rect)

def add_circle(board, layer, center, radius):
    """Add a circle to the board"""
    circle = pcbnew.PCB_SHAPE(board)
    circle.SetShape(pcbnew.SHAPE_T_CIRCLE)
    circle.SetStart(center)
    circle.SetRadius(radius)
    circle.SetLayer(layer)
    board.Add(circle)

def add_text(board, layer, pos, text, size=1.0):
    """Add text to the board"""
    txt = pcbnew.PCB_TEXT(board)
    txt.SetText(text)
    txt.SetPosition(pos)
    txt.SetTextSize(pcbnew.VECTOR2I(int(size * 1000000), int(size * 1000000)))
    txt.SetLayer(layer)
    board.Add(txt)

def main():
    # Create a new board
    board = pcbnew.LoadBoard(r'C:\Users\kevin\ai_bench\image-models-20261020\kicad\hard-task-bare.kicad_pcb')
    
    # Define constants
    table_width = 2000000  # 2000mm
    table_height = 1000000  # 1000mm
    pocket_radius = 50000   # 50mm
    ball_radius = 25000     # 25mm
    cue_stick_width = 10000  # 10mm
    
    # Center of the table
    center_x = table_width // 2
    center_y = table_height // 2
    
    # Table rectangle (outline)
    table_start = pcbnew.VECTOR2I(center_x - table_width//2, center_y - table_height//2)
    table_end = pcbnew.VECTOR2I(center_x + table_width//2, center_y + table_height//2)
    add_rectangle(board, pcbnew.F_SilkS, table_start, table_end, 10000)
    
    # Pockets (circles at corners and midpoints of long sides)
    pockets = [
        # Corners
        pcbnew.VECTOR2I(center_x - table_width//2, center_y - table_height//2),  # bottom-left
        pcbnew.VECTOR2I(center_x + table_width//2, center_y - table_height//2),  # bottom-right
        pcbnew.VECTOR2I(center_x - table_width//2, center_y + table_height//2),  # top-left
        pcbnew.VECTOR2I(center_x + table_width//2, center_y + table_height//2),  # top-right
        # Midpoints of long sides
        pcbnew.VECTOR2I(center_x, center_y - table_height//2),  # bottom-center
        pcbnew.VECTOR2I(center_x, center_y + table_height//2)   # top-center
    ]
    
    for pocket in pockets:
        add_circle(board, pcbnew.F_SilkS, pocket, pocket_radius)
    
    # Object balls arranged in triangle rack (right side)
    # Positions for rows of 1, 2, 3, 4, 5 balls
    ball_positions = []
    row_start_x = center_x + table_width//2 - 100000  # Near right edge
    row_y = center_y
    
    # Row 1 (1 ball)
    ball_positions.append((row_start_x, row_y))
    
    # Row 2 (2 balls)
    row_y -= ball_radius * 2
    ball_positions.append((row_start_x - ball_radius, row_y))
    ball_positions.append((row_start_x + ball_radius, row_y))
    
    # Row 3 (3 balls)
    row_y -= ball_radius * 2
    ball_positions.append((row_start_x - ball_radius * 2, row_y))
    ball_positions.append((row_start_x, row_y))
    ball_positions.append((row_start_x + ball_radius * 2, row_y))
    
    # Row 4 (4 balls)
    row_y -= ball_radius * 2
    ball_positions.append((row_start_x - ball_radius * 3, row_y))
    ball_positions.append((row_start_x - ball_radius, row_y))
    ball_positions.append((row_start_x + ball_radius, row_y))
    ball_positions.append((row_start_x + ball_radius * 3, row_y))
    
    # Row 5 (5 balls)
    row_y -= ball_radius * 2
    ball_positions.append((row_start_x - ball_radius * 4, row_y))
    ball_positions.append((row_start_x - ball_radius * 2, row_y))
    ball_positions.append((row_start_x, row_y))
    ball_positions.append((row_start_x + ball_radius * 2, row_y))
    ball_positions.append((row_start_x + ball_radius * 4, row_y))
    
    # Draw balls
    for pos in ball_positions:
        ball_pos = pcbnew.VECTOR2I(pos[0], pos[1])
        add_circle(board, pcbnew.F_SilkS, ball_pos, ball_radius)
    
    # Cue ball (left of rack)
    cue_ball_x = center_x - table_width//2 + 100000
    cue_ball_y = center_y
    cue_ball_pos = pcbnew.VECTOR2I(cue_ball_x, cue_ball_y)
    add_circle(board, pcbnew.F_SilkS, cue_ball_pos, ball_radius)
    
    # Cue stick (long thin rectangle)
    # Start at left end of table, angled toward cue ball
    stick_start = pcbnew.VECTOR2I(center_x - table_width//2, center_y)
    stick_end = pcbnew.VECTOR2I(cue_ball_x - 50000, cue_ball_y)
    
    # Calculate angle for stick
    dx = cue_ball_x - (center_x - table_width//2)
    dy = cue_ball_y - center_y
    
    # Normalize and scale to make it a thin rectangle
    length = math.sqrt(dx*dx + dy*dy)
    if length > 0:
        nx = -dy / length
        ny = dx / length
        
        # Make stick width small
        half_width = cue_stick_width // 2
        
        # Calculate corners of the rectangle
        p1 = pcbnew.VECTOR2I(
            int(stick_start.x + nx * half_width),
            int(stick_start.y + ny * half_width)
        )
        p2 = pcbnew.VECTOR2I(
            int(stick_start.x - nx * half_width),
            int(stick_start.y - ny * half_width)
        )
        p3 = pcbnew.VECTOR2I(
            int(stick_end.x - nx * half_width),
            int(stick_end.y - ny * half_width)
        )
        p4 = pcbnew.VECTOR2I(
            int(stick_end.x + nx * half_width),
            int(stick_end.y + ny * half_width)
        )
        
        # Draw the cue stick as a polygon
        stick_poly = pcbnew.PCB_SHAPE(board)
        stick_poly.SetShape(pcbnew.SHAPE_T_POLYGON)
        stick_poly.SetLayer(pcbnew.F_SilkS)
        points = [p1, p2, p3, p4]
        stick_poly.SetPolyPoints(points)
        board.Add(stick_poly)
    
    # Add label "CUE" near cue ball
    cue_label_pos = pcbnew.VECTOR2I(cue_ball_x - 100000, cue_ball_y + 50000)
    add_text(board, pcbnew.F_SilkS, cue_label_pos, "CUE", size=0.8)
    
    # Save the board
    pcbnew.SaveBoard(r'C:\Users\kevin\ai_bench\image-models-20261020\kicad\hard-task-bare.kicad_pcb', board)

if __name__ == "__main__":
    main()
