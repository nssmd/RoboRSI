---
name: find_pixel
kind: base
robot: libero
category: perception
version: 0.1.0
description: Ask the shared object detector to point at the pixel of a named object in the latest camera frame. Feed the returned (u,v) to unproject_pixel for world XYZ.
args:
  object:   { type: string, required: true, description: "what to find (e.g. 'red mug', 'alphabet soup can')" }
  location: { type: string, description: "which part, metadata for you (e.g. 'top center')" }
returns:
  ok: bool
  u: int
  v: int
  confidence: float
when_to_use: |
  After look(). This is how you localize an object from the current image:
  find_pixel(object) -> (u,v) -> unproject_pixel(u,v) ->
  world XYZ. Use a concrete noun phrase; look() again if the object moved.
metadata:
  tags: [perception, grounding, pixel, pure-vision, base_skill]
---

# find_pixel (LIBERO)

## Overview
Ground a named object to a pixel centroid in the current head-camera frame using
the shared Grounding-DINO + SAM detector. The input is the rendered RGB image.

## Prerequisites
- A fresh frame from `look()` (sets the head-camera image).

## Phases
1. Snapshot the head-camera image.
2. Detect the noun phrase; take the top mask's centroid.
3. Return `(u, v)` + confidence.

## Success criteria
- `ok=True` with a `(u, v)` inside the image; pass it to `unproject_pixel`.

## Failure modes
- Object not found: use a more concrete noun phrase or `look()` to refresh and
  retry.
