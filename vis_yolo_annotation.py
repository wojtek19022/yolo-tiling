import os
from PIL import Image
import matplotlib.pyplot as plt
import matplotlib.patches as patches

# ==== CONFIG – CHANGE THESE ====
main_path = r""
filename = ""
IMAGE_PATH = os.path.join(main_path,filename + ".jpg")
LABEL_PATH = os.path.join(main_path,filename + ".txt")
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

def visualize_image_with_boxes(image_path, label_path):
    # Load image
    img = Image.open(image_path).convert("RGB")
    img_width, img_height = img.size

    # Load YOLO boxes
    boxes = load_yolo_annotations(label_path, img_width, img_height)

    # Plot
    fig, ax = plt.subplots()
    ax.imshow(img)
    ax.set_title(os.path.basename(image_path))
    ax.axis("off")

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

        # Optional: draw class id text
        ax.text(
            x_min,
            y_min - 2,
            str(cls),
            fontsize=10,
            color='yellow',
            bbox=dict(facecolor='black', alpha=0.5, edgecolor='none')
        )

    plt.show()

if __name__ == "__main__":
    visualize_image_with_boxes(IMAGE_PATH, LABEL_PATH)
