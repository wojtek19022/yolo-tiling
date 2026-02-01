#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Optional, Tuple, Dict

from affine import Affine
from PIL import Image
import rasterio


# ---------- worldfile helpers ----------

def worldfile_ext(img: Path) -> str:
    ext = img.suffix.lower()
    return {
        ".jpg": ".jgw",
        ".jpeg": ".jgw",
        ".png": ".pgw",
        ".tif": ".tfw",
        ".tiff": ".tfw",
    }.get(ext, ".wld")


def write_worldfile(path: Path, t: Affine) -> None:
    # ESRI world file 6-line format: A, D, B, E, C, F
    path.write_text(
        f"{t.a:.12f}\n{t.d:.12f}\n{t.b:.12f}\n{t.e:.12f}\n{t.c:.12f}\n{t.f:.12f}\n",
        encoding="utf-8",
    )


# ---------- name normalization (your "weird beginning") ----------

LEADING_WEIRD_PREFIX = re.compile(r"^[0-9a-fA-F]{8}-")  # e.g. ffbdc372-


def normalize_tile_name(tile_name: str) -> str:
    """
    Remove leading 'ffbdc372-' style prefix if present.
    Example:
      ffbdc372-20210911_... -> 20210911_...
    """
    return LEADING_WEIRD_PREFIX.sub("", tile_name)


def extract_reference_filename_from_tile(normalized_tile_name: str) -> Optional[str]:
    """
    Tiles contain the reference JPG name inside them, e.g.:
      20210911_..._Nadir.jpg_119_sharp_augment_2_0_12_34.jpg

    This function returns:
      20210911_..._Nadir.jpg
    (everything up to first .jpg/.jpeg)
    """
    m = re.search(r"\.(jpg|jpeg)", normalized_tile_name, re.IGNORECASE)
    if not m:
        return None
    end = m.end()  # include extension
    return normalized_tile_name[:end]


# ---------- tile parsing ----------

def parse_row_col(tile_name: str, rc_pattern: re.Pattern) -> Optional[Tuple[int, int]]:
    """
    Default expects trailing _<row>_<col>.<ext>
    """
    m = rc_pattern.search(tile_name)
    if not m:
        return None
    return int(m.group("row")), int(m.group("col"))


# ---------- reference georef (from .aux/.aux.xml via GDAL) ----------

def read_reference_georef(ref_jpg: Path) -> Tuple[Affine, str]:
    """
    Reads transform + CRS from reference image.
    rasterio/GDAL will use .aux/.aux.xml if present.
    """
    with rasterio.open(ref_jpg) as src:
        if src.crs is None:
            raise RuntimeError(f"Reference has no CRS (even after reading .aux/.aux.xml): {ref_jpg}")
        return src.transform, src.crs.to_wkt()


# ---------- main ----------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Create per-tile .jgw + .prj using georef stored in reference JPG .aux/.aux.xml files."
    )
    ap.add_argument("--refs", required=True, help="Folder containing reference JPGs (with .aux/.aux.xml).")
    ap.add_argument("--tiles", required=True, help="Folder containing tile images.")
    ap.add_argument("--tile-size", type=int, default=512, help="Tile size in px (default 512).")
    ap.add_argument("--stride", type=int, default=None,
                    help="Stride in px (default = tile-size). If overlap, stride = tile_size - overlap.")
    ap.add_argument("--recursive-refs", action="store_true", help="Scan refs recursively.")
    ap.add_argument("--recursive-tiles", action="store_true", help="Scan tiles recursively.")
    ap.add_argument("--ref-glob", default="*.jpg", help="Glob for reference JPGs (default *.jpg).")
    ap.add_argument(
        "--tile-regex",
        default=r"_(?P<row>\d+)_(?P<col>\d+)\.(jpg|jpeg|png)$",
        help="Regex to parse tile row/col at end. Must include named groups row and col.",
    )
    args = ap.parse_args()

    refs_dir = Path(args.refs)
    tiles_dir = Path(args.tiles)
    if not refs_dir.exists():
        raise SystemExit(f"Refs folder not found: {refs_dir}")
    if not tiles_dir.exists():
        raise SystemExit(f"Tiles folder not found: {tiles_dir}")

    stride = args.stride if args.stride is not None else args.tile_size
    rc_pattern = re.compile(args.tile_regex, re.IGNORECASE)

    # Index reference JPGs by filename (WITH extension) and by stem (fallback)
    ref_iter = refs_dir.rglob(args.ref_glob) if args.recursive_refs else refs_dir.glob(args.ref_glob)
    refs_by_name: Dict[str, Path] = {}
    refs_by_stem: Dict[str, Path] = {}

    for p in ref_iter:
        if p.is_file():
            refs_by_name[p.name] = p
            refs_by_stem[p.stem] = p

    if not refs_by_name:
        raise SystemExit(f"No reference JPGs found in {refs_dir} using glob {args.ref_glob!r}")

    # Cache georef to avoid reopening reference files repeatedly
    georef_cache: Dict[Path, Tuple[Affine, str]] = {}

    tile_iter = tiles_dir.rglob("*") if args.recursive_tiles else tiles_dir.glob("*")

    processed = 0
    skipped = 0
    no_ref = 0
    ref_errors = 0

    for tile in tile_iter:
        if not tile.is_file():
            continue

        # Parse row/col from actual file name (not normalized) or normalized — either is fine for tail pattern.
        rc = parse_row_col(tile.name, rc_pattern)
        if rc is None:
            skipped += 1
            continue
        row, col = rc

        # Normalize: remove leading ffbdc372-
        norm_name = normalize_tile_name(tile.name)

        # Extract reference image filename embedded in tile name
        ref_filename = extract_reference_filename_from_tile(norm_name)
        if ref_filename is None:
            skipped += 1
            continue

        # Find matching reference path
        ref_path = refs_by_name.get(ref_filename)
        if ref_path is None:
            # Fallback: try stem match if tile used ".jpg" inside but reference might be different case, etc.
            ref_stem = Path(ref_filename).stem
            ref_path = refs_by_stem.get(ref_stem)

        if ref_path is None:
            no_ref += 1
            continue

        # Load reference georef (from aux) once
        if ref_path not in georef_cache:
            try:
                georef_cache[ref_path] = read_reference_georef(ref_path)
            except Exception as e:
                ref_errors += 1
                print(f"[REF ERROR] {ref_path} -> {e}")
                continue

        base_transform, crs_wkt = georef_cache[ref_path]

        # Make sure tile is readable (edge tiles can be smaller)
        try:
            with Image.open(tile) as im:
                _w, _h = im.size
        except Exception:
            skipped += 1
            continue

        # Compute pixel offset of tile in reference image
        row_off = row * stride
        col_off = col * stride

        # Shift reference transform by pixel offset (works also for rotated transforms)
        tile_transform = base_transform * Affine.translation(col_off, row_off)

        # Write sidecars PER TILE
        write_worldfile(tile.with_suffix(worldfile_ext(tile)), tile_transform)
        tile.with_suffix(".prj").write_text(crs_wkt, encoding="utf-8")

        processed += 1

    print("Done.")
    print(f"Processed tiles (wrote .jgw + .prj for each): {processed}")
    print(f"Skipped (name mismatch / unreadable): {skipped}")
    print(f"No matching reference found: {no_ref}")
    print(f"Reference read errors: {ref_errors}")
    print(f"Stride used: {stride}px (tile-size arg: {args.tile_size}px)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
