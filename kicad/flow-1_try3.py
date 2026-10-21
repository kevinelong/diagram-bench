import pcbnew

def create_flowchart_board():
    # Create empty board
    board = pcbnew.CreateEmptyBoard()
    
    # Set board dimensions (0 to 90 mm horizontally, 0 to 60 mm vertically)
    board.SetWidth(pcbnew.FromMM(90))
    board.SetHeight(pcbnew.FromMM(60))
    
    # Define coordinates in nanometers
    mm_to_nm = 1000000
    start_x = 45 * mm_to_nm  # Center horizontally
    box_width = 12000000     # 12mm wide
    box_height = 6000000    # 6mm high
    box_spacing = 8000000   # 8mm vertical spacing
    
    # Create boxes
    y_positions = [0, box_spacing, 2 * box_spacing, 3 * box_spacing]
    
    # START box
    start_box = pcbnew.PCB_SHAPE(board)
    start_box.SetShape(pcbnew.SHAPE_T_RECT)
    start_box.SetLayer(pcbnew.F_SilkS)
    start_box.SetStart(pcbnew.VECTOR2I(start_x - box_width // 2, y_positions[0] - box_height // 2))
    start_box.SetEnd(pcbnew.VECTOR2I(start_x + box_width // 2, y_positions[0] + box_height // 2))
    start_box.SetWidth(1000000)  # 1mm line width
    board.Add(start_box)
    
    # VALIDATE box
    validate_box = pcbnew.PCB_SHAPE(board)
    validate_box.SetShape(pcbnew.SHAPE_T_RECT)
    validate_box.SetLayer(pcbnew.F_SilkS)
    validate_box.SetStart(pcbnew.VECTOR2I(start_x - box_width // 2, y_positions[1] - box_height // 2))
    validate_box.SetEnd(pcbnew.VECTOR2I(start_x + box_width // 2, y_positions[1] + box_height // 2))
    validate_box.SetWidth(1000000)  # 1mm line width
    board.Add(validate_box)
    
    # STORE box
    store_box = pcbnew.PCB_SHAPE(board)
    store_box.SetShape(pcbnew.SHAPE_T_RECT)
    store_box.SetLayer(pcbnew.F_SilkS)
    store_box.SetStart(pcbnew.VECTOR2I(start_x - box_width // 2, y_positions[2] - box_height // 2))
    store_box.SetEnd(pcbnew.VECTOR2I(start_x + box_width // 2, y_positions[2] + box_height // 2))
    store_box.SetWidth(1000000)  # 1mm line width
    board.Add(store_box)
    
    # DONE box
    done_box = pcbnew.PCB_SHAPE(board)
    done_box.SetShape(pcbnew.SHAPE_T_RECT)
    done_box.SetLayer(pcbnew.F_SilkS)
    done_box.SetStart(pcbnew.VECTOR2I(start_x - box_width // 2, y_positions[3] - box_height // 2))
    done_box.SetEnd(pcbnew.VECTOR2I(start_x + box_width // 2, y_positions[3] + box_height // 2))
    done_box.SetWidth(1000000)  # 1mm line width
    board.Add(done_box)
    
    # Create arrows (lines)
    arrow_length = 4000000  # 4mm
    
    # Arrow from START to VALIDATE
    start_arrow = pcbnew.PCB_SHAPE(board)
    start_arrow.SetShape(pcbnew.SHAPE_T_SEGMENT)
    start_arrow.SetLayer(pcbnew.F_SilkS)
    start_arrow.SetStart(pcbnew.VECTOR2I(start_x, y_positions[0] + box_height // 2))
    start_arrow.SetEnd(pcbnew.VECTOR2I(start_x, y_positions[1] - box_height // 2))
    start_arrow.SetWidth(1000000)  # 1mm line width
    board.Add(start_arrow)
    
    # Arrow from VALIDATE to STORE
    validate_arrow = pcbnew.PCB_SHAPE(board)
    validate_arrow.SetShape(pcbnew.SHAPE_T_SEGMENT)
    validate_arrow.SetLayer(pcbnew.F_SilkS)
    validate_arrow.SetStart(pcbnew.VECTOR2I(start_x, y_positions[1] + box_height // 2))
    validate_arrow.SetEnd(pcbnew.VECTOR2I(start_x, y_positions[2] - box_height // 2))
    validate_arrow.SetWidth(1000000)  # 1mm line width
    board.Add(validate_arrow)
    
    # Arrow from STORE to DONE
    store_arrow = pcbnew.PCB_SHAPE(board)
    store_arrow.SetShape(pcbnew.SHAPE_T_SEGMENT)
    store_arrow.SetLayer(pcbnew.F_SilkS)
    store_arrow.SetStart(pcbnew.VECTOR2I(start_x, y_positions[2] + box_height // 2))
    store_arrow.SetEnd(pcbnew.VECTOR2I(start_x, y_positions[3] - box_height // 2))
    store_arrow.SetWidth(1000000)  # 1mm line width
    board.Add(store_arrow)
    
    # Add text labels
    # START text
    start_text = pcbnew.PCB_TEXT(board)
    start_text.SetText("START")
    start_text.SetLayer(pcbnew.F_SilkS)
    start_text.SetTextPosition(pcbnew.VECTOR2I(start_x, y_positions[0]))
    start_text.SetTextSize(pcbnew.VECTOR2I(1500000, 1500000))  # 1.5mm text size
    start_text.SetTextThickness(300000)  # 0.3mm text thickness
    board.Add(start_text)
    
    # VALIDATE text
    validate_text = pcbnew.PCB_TEXT(board)
    validate_text.SetText("VALIDATE")
    validate_text.SetLayer(pcbnew.F_SilkS)
    validate_text.SetTextPosition(pcbnew.VECTOR2I(start_x, y_positions[1]))
    validate_text.SetTextSize(pcbnew.VECTOR2I(1500000, 1500000))  # 1.5mm text size
    validate_text.SetTextThickness(300000)  # 0.3mm text thickness
    board.Add(validate_text)
    
    # STORE text
    store_text = pcbnew.PCB_TEXT(board)
    store_text.SetText("STORE")
    store_text.SetLayer(pcbnew.F_SilkS)
    store_text.SetTextPosition(pcbnew.VECTOR2I(start_x, y_positions[2]))
    store_text.SetTextSize(pcbnew.VECTOR2I(1500000, 1500000))  # 1.5mm text size
    store_text.SetTextThickness(300000)  # 0.3mm text thickness
    board.Add(store_text)
    
    # DONE text
    done_text = pcbnew.PCB_TEXT(board)
    done_text.SetText("DONE")
    done_text.SetLayer(pcbnew.F_SilkS)
    done_text.SetTextPosition(pcbnew.VECTOR2I(start_x, y_positions[3]))
    done_text.SetTextSize(pcbnew.VECTOR2I(1500000, 1500000))  # 1.5mm text size
    done_text.SetTextThickness(300000)  # 0.3mm text thickness
    board.Add(done_text)
    
    # Save board
    pcbnew.SaveBoard(r'C:\Users\kevin\ai_bench\image-models-20261020\kicad\flow-1.kicad_pcb', board)

# Execute the function
create_flowchart_board()
