import pcbnew

def create_flowchart_board():
    # Create empty board
    board = pcbnew.CreateEmptyBoard()
    
    # Set board dimensions (0 to 90 mm horizontally, 0 to 60 mm vertically)
    board.SetWidth(pcbnew.FromMM(90))
    board.SetHeight(pcbnew.FromMM(60))
    
    # Define coordinates in nanometers
    mm_to_nm = 1000000
    
    # Box dimensions
    box_width = 15 * mm_to_nm
    box_height = 8 * mm_to_nm
    box_spacing = 5 * mm_to_nm
    
    # Starting position for first box (centered horizontally)
    start_x = (90 * mm_to_nm - box_width) // 2
    
    # Create boxes
    boxes = []
    y_positions = [0, 0, 0, 0]
    
    # Calculate Y positions for each box
    y_positions[0] = 5 * mm_to_nm  # START
    y_positions[1] = y_positions[0] + box_height + box_spacing  # VALIDATE
    y_positions[2] = y_positions[1] + box_height + box_spacing  # STORE
    y_positions[3] = y_positions[2] + box_height + box_spacing  # DONE
    
    # Create rectangles for boxes
    for i, y in enumerate(y_positions):
        box = pcbnew.PCB_SHAPE(board)
        box.SetShape(pcbnew.SHAPE_T_RECT)
        box.SetLayer(pcbnew.F_SilkS)
        box.SetStart(pcbnew.VECTOR2I(start_x, y))
        box.SetEnd(pcbnew.VECTOR2I(start_x + box_width, y + box_height))
        box.SetWidth(100000)  # 0.1 mm width
        board.Add(box)
        boxes.append(box)
    
    # Create arrows (lines with arrowheads)
    arrow_length = 10 * mm_to_nm
    arrow_width = 2 * mm_to_nm
    
    for i in range(3):  # Three arrows between four boxes
        y_start = y_positions[i] + box_height
        y_end = y_positions[i+1]
        
        # Create line
        line = pcbnew.PCB_SHAPE(board)
        line.SetShape(pcbnew.SHAPE_T_SEGMENT)
        line.SetLayer(pcbnew.F_SilkS)
        line.SetStart(pcbnew.VECTOR2I(start_x + box_width // 2, y_start))
        line.SetEnd(pcbnew.VECTOR2I(start_x + box_width // 2, y_end))
        line.SetWidth(100000)  # 0.1 mm width
        board.Add(line)
    
    # Create text labels
    texts = ["START", "VALIDATE", "STORE", "DONE"]
    
    for i, (text, y) in enumerate(zip(texts, y_positions)):
        text_obj = pcbnew.PCB_TEXT(board)
        text_obj.SetText(text)
        text_obj.SetLayer(pcbnew.F_SilkS)
        text_obj.SetTextPosition(pcbnew.VECTOR2I(start_x + box_width // 2, y + box_height // 2))
        text_obj.SetTextSize(pcbnew.VECTOR2I(1500000, 1500000))  # 1.5 mm text size
        text_obj.SetTextThickness(150000)  # 0.15 mm thickness
        board.Add(text_obj)
    
    # Save the board
    pcbnew.SaveBoard(r'C:\Users\kevin\ai_bench\image-models-20261020\kicad\flow-1.kicad_pcb', board)

create_flowchart_board()
