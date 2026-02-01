import os
from PIL import Image
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from pathlib import Path

# ==== CONFIG – CHANGE THESE ====
main_path = r"C:\Users\Wojtek\Documents\GitHub\Tools\magisterka\temp_augment_data_dir_LARGER_augment_resized"
# ===============================

def load_yolo_annotations(label_path, img_width, img_height):
    """
    Reads YOLO txt file and returns list of:
    (class_id, x_min, y_min, box_width, box_height) in pixel coordinates.
    """
    boxes = []

    if not os.path.exists(label_path):
        print(f"Label file not found: {label_path}")
        return boxes

    with open(label_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            parts = line.split()
            if len(parts) != 5:
                # Expect: class xc yc w h
                print(f"Skipping invalid line: {line}")
                continue

            cls = int(parts[0])
            xc, yc, w, h = map(float, parts[1:])

            # Convert normalized to pixel coords
            x_center = xc * img_width
            y_center = yc * img_height
            box_w    = w  * img_width
            box_h    = h  * img_height

            x_min = x_center - box_w / 2
            y_min = y_center - box_h / 2

            boxes.append((cls, x_min, y_min, box_w, box_h))

    return boxes

def visualize_image_with_boxes(main_path,image_path, label_path):
    # Load image
    img = Image.open(image_path).convert("RGB")
    img_width, img_height = img.size

    # Load YOLO boxes
    boxes = load_yolo_annotations(label_path, img_width, img_height)

    # Plot
    fig, ax = plt.subplots(1,1, figsize=(12,8))
    ax.imshow(img)
    ax.set_title(os.path.basename(image_path))
    # ax.axis("off")

    # Add boxes
    for cls, x_min, y_min, box_w, box_h in boxes:
        # Rectangle: (x, y), width, height
        rect = patches.Rectangle(
            (x_min, y_min),
            box_w,
            box_h,
            linewidth=2,
            edgecolor='r',  # red boxes
            facecolor='none'
        )
        ax.add_patch(rect)
        print("BBOX: ", x_min, y_min, box_w, box_h)
        # Optional: draw class id text
        ax.text(
            x_min,
            y_min - 2,
            str(cls),
            fontsize=10,
            color='yellow',
            bbox=dict(facecolor='black', alpha=0.5, edgecolor='none')
        )

    # Save the plot instead of showing (more reliable)
    out_path = r"C:\Users\Wojtek\Documents\GitHub\Tools\magisterka\temp_augment_data_dir_LARGER_augment_resized\visualizations"
    plt.savefig(os.path.join(out_path,Path(IMAGE_PATH).stem+"_vis.jpg"), bbox_inches='tight', dpi=150)
    plt.close(fig)  # Close to free memory
    print(f"Visualization saved to {Path(IMAGE_PATH).stem}_vis.jpg")

if __name__ == "__main__":
    count = 0
    for filename in os.listdir(os.path.join(main_path,'images')):
        # if filename.endswith(".jpg"):
        filename = Path(filename).stem
        IMAGE_PATH = os.path.join(main_path,"images",filename+ ".jpg")
        LABEL_PATH = os.path.join(main_path,"labels",filename + ".txt")
        visualize_image_with_boxes(main_path,IMAGE_PATH, LABEL_PATH)
        count += 1
        if count == 200:
            break
        
