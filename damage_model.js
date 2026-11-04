// FEMA Hazus 4.0 riverine RES1 building damage functions, from the archived
// official FAST Building_DDF_Riverine_LUT_Hazus4p0.csv lookup table. The current
// Hazus manual still lists these function IDs, but these coefficients are 4.0.
// Each array is percent structure damage at first-floor depths -4, -3, ... 24 ft.
const HAZUS_CURVES = {
  R11N: { id: 129, label: "1 story, no basement", values: [0,0,0,3,13,23,32,40,47,53,59,63,67,71,73,75,77,79,80,81,81,82,82,83,83,83,84,84,84] },
  R11B: { id: 704, label: "1 story, basement", values: [0,0,14,19,26,32,39,46,52,59,65,70,74,78,80,81,81,81,81,81,81,81,81,81,81,81,81,81,81] },
  R12N: { id: 107, label: "2 stories, no basement", values: [0,0,0,0,11,12,14,18,20,22,24,26,30,34,38,39,40,42,43,44,45,47,48,49,50,52,53,54,56] },
  R12B: { id: 108, label: "2 stories, basement", values: [4,4,8,14,19,21,26,29,34,39,44,50,55,57,59,61,63,65,66,68,69,71,72,74,75,77,79,80,82] },
};

function estimateStructureDamage(groundDepthFt, firstFloorHeightFt, curve) {
  if (!Number.isFinite(groundDepthFt) || !Number.isFinite(firstFloorHeightFt) || !curve) return null;
  const floorDepth = groundDepthFt - firstFloorHeightFt;
  if (floorDepth < -4 || floorDepth > 24) return null; // No extrapolation.
  const lower = Math.floor(floorDepth);
  const fraction = floorDepth - lower;
  const index = lower + 4;
  return curve.values[index] + fraction * (curve.values[Math.min(index + 1, 28)] - curve.values[index]);
}

if (typeof module !== "undefined") module.exports = { HAZUS_CURVES, estimateStructureDamage };
