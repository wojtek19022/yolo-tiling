#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Optional, Tuple, Dict
import xml.etree.ElementTree as ET

from PIL import Image


LEADING_WEIRD = re.compile(r"^[0-9a-fA-F]{8}-")            # 00bb88ea-
LAST_TWO = re.compile(r"_(?P<a>\d+)_(?P<b>\d+)$")          # ending _2_1
# base + frame: "...<base>.jpg_<frame>_..."
REF_FROM_TILE = re.compile(r"(?P<base>.+?)\.(jpg|jpeg)_(?P<frame>\d+)_", re.IGNORECASE)


def normalize_stem(stem: str) -> str:
    return LEADING_WEIRD.sub("", stem)


def parse_last_two(stem: str) -> Optional[Tuple[int, int]]:
    m = LAST_TWO.search(stem)
    if not m:
        return None
    return int(m.group("a")), int(m.group("b"))


def extract_ref_base_and_frame(normalized_stem: str) -> Optional[Tuple[str, Optional[str]]]:
    m = REF_FROM_TILE.search(normalized_stem)
    if m:
        return m.group("base"), m.group("frame")
    # fallback: just "...<base>.jpg..."
    m2 = re.search(r"(?P<base>.+?)\.(jpg|jpeg)", normalized_stem, re.IGNORECASE)
    if not m2:
        return None
    return m2.group("base"), None


def build_ref_index(refs_dir: Path, recursive: bool) -> Dict[str, Path]:
    it = refs_dir.rglob("*.jp*g") if recursive else refs_dir.glob("*.jp*g")
    return {p.name.lower(): p for p in it if p.is_file()}


def choose_reference(ref_index: Dict[str, Path], base: str, frame: Optional[str]) -> Optional[Path]:
    """
    Try common on-disk names:
      base.jpg
      base_<frame>.jpg
      base-<frame>.jpg
    """
    candidates = [f"{base}.jpg", f"{base}.jpeg"]
    if frame:
        candidates += [
            f"{base}_{frame}.jpg", f"{base}_{frame}.jpeg",
            f"{base}-{frame}.jpg", f"{base}-{frame}.jpeg",
        ]
    for c in candidates:
        p = ref_index.get(c.lower())
        if p:
            return p

    # fuzzy fallback
    base_l = base.lower()
    frame_l = frame.lower() if frame else None
    best = None
    best_len = 10**9
    for name_l, p in ref_index.items():
        if not name_l.startswith(base_l):
            continue
        if frame_l and frame_l not in name_l:
            continue
        if len(name_l) < best_len:
            best = p
            best_len = len(name_l)
    return best


def parse_geotransform_text(gt_text: str) -> Tuple[float, float, float, float, float, float]:
    # input like: "  6.38e+05, -2.26e-02, ..."
    parts = [p.strip() for p in gt_text.replace("\n", " ").split(",")]
    if len(parts) != 6:
        raise ValueError(f"GeoTransform does not have 6 values: {gt_text!r}")
    return tuple(float(x) for x in parts)  # type: ignore


def format_geotransform(gt: Tuple[float, float, float, float, float, float]) -> str:
    # Match GDAL-ish style: leading spaces + comma-separated
    return "  " + ", ".join(f"{v:.16e}" for v in gt)


def shifted_geotransform(gt: Tuple[float, float, float, float, float, float],
                         col_off_px: int,
                         row_off_px: int) -> Tuple[float, float, float, float, float, float]:
    """
    GDAL geotransform:
      Xgeo = GT0 + col*GT1 + row*GT2
      Ygeo = GT3 + col*GT4 + row*GT5
    """
    GT0, GT1, GT2, GT3, GT4, GT5 = gt
    new_GT0 = GT0 + col_off_px * GT1 + row_off_px * GT2
    new_GT3 = GT3 + col_off_px * GT4 + row_off_px * GT5
    return (new_GT0, GT1, GT2, new_GT3, GT4, GT5)


def ensure_metadata_blocks(root: ET.Element) -> None:
    """
    Your template already has them. This is a no-op placeholder in case
    some references miss the blocks (we don't invent values).
    """
    return


def update_geotransform_in_auxxml(aux_tree: ET.ElementTree, new_gt: Tuple[float, float, float, float, float, float]) -> None:
    root = aux_tree.getroot()
    gt_el = root.find("GeoTransform")
    if gt_el is None:
        # Create it near the top after SRS, to mimic GDAL structure.
        srs_el = root.find("SRS")
        gt_el = ET.Element("GeoTransform")
        if srs_el is not None:
            # insert after SRS
            idx = list(root).index(srs_el) + 1
            root.insert(idx, gt_el)
        else:
            root.insert(0, gt_el)

    gt_el.text = format_geotransform(new_gt)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate tile.jpg.aux.xml by copying reference .aux.xml structure and updating only GeoTransform per tile."
    )
    ap.add_argument("--refs", required=True, help="Folder with reference JPG + reference.jpg.aux.xml")
    ap.add_argument("--tiles", required=True, help="Folder with tile JPGs")
    ap.add_argument("--tile-size", type=int, default=512)
    ap.add_argument("--stride", type=int, default=None)
    ap.add_argument("--recursive-refs", action="store_true")
    ap.add_argument("--recursive-tiles", action="store_true")
    ap.add_argument("--swap-rowcol", action="store_true", help="Interpret last _A_B as col_row instead of row_col")
    ap.add_argument("--tiles-glob", default="*.jp*g")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    refs_dir = Path(args.refs)
    tiles_dir = Path(args.tiles)
    if not refs_dir.exists():
        raise SystemExit(f"Refs folder not found: {refs_dir}")
    if not tiles_dir.exists():
        raise SystemExit(f"Tiles folder not found: {tiles_dir}")

    stride = args.stride if args.stride is not None else args.tile_size

    ref_index = build_ref_index(refs_dir, args.recursive_refs)
    if not ref_index:
        raise SystemExit("No reference JPGs found.")

    # cache parsed reference aux.xml and GT
    ref_cache: Dict[Path, Tuple[ET.ElementTree, Tuple[float, float, float, float, float, float]]] = {}

    tile_iter = tiles_dir.rglob(args.tiles_glob) if args.recursive_tiles else tiles_dir.glob(args.tiles_glob)

    processed = 0
    skipped = 0
    no_ref = 0
    bad_ref = 0

    from tqdm import tqdm
    import os

    for tile_path in tqdm(tile_iter, total=len([file for file in os.listdir(tiles_dir) if file.endswith(".jpg")])):
        if not tile_path.is_file():
            continue

        # only handle actual tiles that end with _A_B
        last = parse_last_two(tile_path.stem)
        if last is None:
            skipped += 1
            continue
        a, b = last
        if args.swap_rowcol:
            tile_col, tile_row = a, b
        else:
            tile_row, tile_col = a, b

        normalized = normalize_stem(tile_path.stem)
        base_frame = extract_ref_base_and_frame(normalized)
        if base_frame is None:
            skipped += 1
            continue
        base, frame = base_frame

        ref_jpg = choose_reference(ref_index, base, frame)
        if ref_jpg is None:
            no_ref += 1
            if args.debug:
                print(f"[NO REF] {tile_path.name} base={base!r} frame={frame!r}")
            continue

        ref_aux = Path(str(ref_jpg) + ".aux.xml")  # reference.jpg.aux.xml
        if not ref_aux.exists():
            bad_ref += 1
            if args.debug:
                print(f"[BAD REF] Missing aux: {ref_aux}")
            continue

        # cache reference tree + base GT
        if ref_jpg not in ref_cache:
            try:
                aux_tree = ET.parse(ref_aux)
                root = aux_tree.getroot()
                if root.tag != "PAMDataset":
                    raise RuntimeError(f"Unexpected root tag: {root.tag}")
                gt_el = root.find("GeoTransform")
                if gt_el is None or not (gt_el.text and gt_el.text.strip()):
                    raise RuntimeError("Reference aux.xml missing GeoTransform text")
                ref_gt = parse_geotransform_text(gt_el.text)
                ensure_metadata_blocks(root)
                ref_cache[ref_jpg] = (aux_tree, ref_gt)
            except Exception as e:
                bad_ref += 1
                print(f"[BAD REF] {ref_aux.name}: {e}")
                continue

        # read tile size (not strictly needed unless you later handle flips)
        try:
            with Image.open(tile_path) as im:
                _w, _h = im.size
        except Exception:
            skipped += 1
            continue

        aux_tree_template, ref_gt = ref_cache[ref_jpg]

        # Compute tile GT using tile indices -> pixel offsets
        row_off = tile_row * stride
        col_off = tile_col * stride
        tile_gt = shifted_geotransform(ref_gt, col_off_px=col_off, row_off_px=row_off)

        # IMPORTANT: clone the tree so we don't mutate the cached template
        aux_tree = ET.ElementTree(ET.fromstring(ET.tostring(aux_tree_template.getroot(), encoding="utf-8")))

        update_geotransform_in_auxxml(aux_tree, tile_gt)

        out_aux = Path(str(tile_path) + ".aux.xml")
        aux_tree.write(out_aux, encoding="UTF-8", xml_declaration=False)

        processed += 1
        if args.debug and processed <= 5:
            print(f"[OK] {tile_path.name} -> {out_aux.name} using ref={ref_jpg.name} row={tile_row} col={tile_col}")

    print("Done.")
    print(f"Processed tiles (wrote aux.xml): {processed}")
    print(f"No matching reference: {no_ref}")
    print(f"Skipped (non-tiles or parse/read issues): {skipped}")
    print(f"Bad references (missing/broken aux.xml): {bad_ref}")
    print(f"Stride used: {stride}px (tile-size={args.tile_size}px) swap-rowcol={args.swap_rowcol}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
