# Rose Tattoo room-image extraction

PC `RES*.RRM` resources contain image chunks in addition to the painted room
background. Extract every available scene, or pass `--scenes 26 36` to select
rooms:

```sh
python3 tools/extract_rosetattoo_room_sprites.py --data-dir scummvm \
  --output-dir extracted/room-sprites
```

The output directory must be new or empty; use a different directory to rerun
an extraction. Game artwork and metadata stay in the ignored `extracted/`
tree. The extractor reads the PC room format, not 3DO resources or cAnim
cutscene streams. It does not create an upscaled pack or enable overrides.

The stable key for an embedded image is its scene plus its **one-based**
`Scene::_images` slot. `room_004` corresponds to image chunk ordinal 3 (the
fourth chunk), not the fourth scene object or a filename from the room header.
Frames are **zero-based** `ImageFile` frame indices. For example:

```text
extracted/room-sprites/scene_026/room_004/frame_000.png
extracted/room-sprites/scene_026/room_004/metadata.json
```

The corresponding proposed 2x runtime override key under an override root is
`sprites/scene_026/room_004/frame_000@2x.png`. This extraction does not make
that file or promise that any current runtime consumes it. Missing runtime
support and composition/z-order behavior must be handled separately.

Frames are native-size RGBA using the room's initial VGA palette, with palette
index 255 transparent. Runtime palette changes are not represented. Each
room's metadata records its source, slot, zero-based chunk ordinal, stored and
expanded sizes and source offset; each frame records index, dimensions, native
offset, encoding, and byte offset within the expanded chunk. Header
`max_frames` is retained as metadata, not used to truncate the frame list.
Scene metadata and the root `manifest.json` summarize all chunks and counts.

Run the format tests (Pillow required):

```sh
python3 -m unittest discover -s tools/tests -p test_room_sprite_extraction.py -v
```
