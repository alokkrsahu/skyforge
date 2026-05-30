#!/usr/bin/env python3
"""Generate a 'forest clearing' Gazebo world for Skyforge shows.

Why this exists
---------------
The bare ``default.sdf`` (gray ground plane + sun) is a dull backdrop. PX4 ships a
``forest.sdf``, but it is unusable for a drone *show* as-is:
  * its trees use the FULL tree mesh as collision and stand ~8 m tall, and several
    sit right inside the drone takeoff grid (0..18 m) -> a drone spawns inside a
    trunk and flips instantly, or clips a canopy at the 5 m cruise altitude;
  * it uses stock ODE @ 250 Hz (the ODE integer-overflow crash returns at 42+ drones),
    whereas Skyforge's ``default.sdf`` was edited to DART @ 100 Hz;
  * its name is ``forest`` -> the 5 hardcoded ``/world/default/...`` runtime paths
    (t1 model-remove, t2 GUI readiness poll, LED visual/light_config services) break.

This generator produces a world that:
  * keeps ``<world name="default">`` + DART @ 100 Hz + the sun/atmosphere/spherical
    coords from the stock Skyforge default, so EVERY hardcoded path and the GUI/LEDs
    keep working untouched and big fleets stay crash-free;
  * lays a grass floor over the show area (flat ground decals, harmless);
  * rings the stage with VISUAL-ONLY oak/pine trees (no <collision>) so no drone can
    ever spawn inside or clip one, at any fleet size or formation.

Run:  python3 make_forest_stage.py [output.sdf]
Deploy: cp the output over ~/src/PX4-Autopilot/Tools/simulation/gz/worlds/default.sdf
        (back up the original first).
"""
import math
import sys

# ── Fuel asset URIs (literal spaces, exactly as the cached model.sdf references
#    them — gz resolves these to the local ~/.gz/fuel cache when present). ──────
OAK_MESH  = "https://fuel.gazebosim.org/1.0/openrobotics/models/oak tree/7/files/meshes/oak_tree.dae"
OAK_BRANCH = "https://fuel.gazebosim.org/1.0/openrobotics/models/oak tree/7/files/materials/textures/branch_diffuse.png"
OAK_BARK   = "https://fuel.gazebosim.org/1.0/openrobotics/models/oak tree/7/files/materials/textures/bark_diffuse.png"
PINE_MESH  = "https://fuel.gazebosim.org/1.0/openrobotics/models/pine tree/6/files/meshes/pine_tree.dae"
PINE_BRANCH = "https://fuel.gazebosim.org/1.0/openrobotics/models/pine tree/6/files/materials/textures/branch_2_diffuse.png"
PINE_BARK   = "https://fuel.gazebosim.org/1.0/openrobotics/models/pine tree/6/files/materials/textures/bark_diffuse.png"
GRASS_URI   = "https://fuel.gazebosim.org/1.0/hexarotor/models/grasspatch"

# ── Stage geometry ────────────────────────────────────────────────────────────
# The show lives in the +x+y quadrant: home grid is col*2,row*2 (0..18 m at N=100),
# centroid ~ (9, 9); scaled formations reach ~24 m from the world origin. Keep the
# inner tree line beyond that with margin; trees are visual-only so this is purely
# aesthetic (drones cannot collide with them regardless).
CLEAR_R   = 30.0     # inner radius of the tree line (m) — clears the show footprint
N_INNER, R_INNER = 10, 32.0   # thinned backdrop (was 20) for lighter GUI rendering
N_OUTER, R_OUTER = 6, 44.0    # thinned backdrop (was 14)


def tree_model(name: str, kind: str, x: float, y: float, yaw: float, scale: float) -> str:
    """A visual-only tree (no <collision>): pure backdrop, cannot crash a drone.

    Replicates the cached model's two submesh visuals (Branch, Bark) with their PBR
    albedo maps — which is what gz-sim renders. The legacy OGRE <script> blocks and
    the full-mesh <collision> are intentionally omitted.
    """
    mesh, branch, bark = (
        (OAK_MESH, OAK_BRANCH, OAK_BARK) if kind == "oak"
        else (PINE_MESH, PINE_BRANCH, PINE_BARK)
    )
    s = f"{scale:.3f}"
    return f"""    <model name="{name}">
      <static>true</static>
      <pose>{x:.3f} {y:.3f} 0 0 0 {yaw:.4f}</pose>
      <link name="link">
        <visual name="branch">
          <geometry>
            <mesh>
              <uri>{mesh}</uri>
              <submesh><name>Branch</name></submesh>
              <scale>{s} {s} {s}</scale>
            </mesh>
          </geometry>
          <material>
            <double_sided>true</double_sided>
            <diffuse>1.0 1.0 1.0 1</diffuse>
            <pbr><metal><albedo_map>{branch}</albedo_map></metal></pbr>
          </material>
        </visual>
        <visual name="bark">
          <geometry>
            <mesh>
              <uri>{mesh}</uri>
              <submesh><name>Bark</name></submesh>
              <scale>{s} {s} {s}</scale>
            </mesh>
          </geometry>
          <material>
            <diffuse>1.0 1.0 1.0 1</diffuse>
            <pbr><metal><albedo_map>{bark}</albedo_map></metal></pbr>
          </material>
        </visual>
      </link>
    </model>
"""


def grass_tile(name: str, x: float, y: float) -> str:
    # Flat 15x15 grass decal at z=0.01 (1 cm above the gray ground plane so the
    # grass wins the depth test; the full ground_plane below stays the contact
    # surface). grasspatch's own collision is a coplanar ground plane — harmless.
    return (f'    <include>\n'
            f'      <uri>{GRASS_URI}</uri>\n'
            f'      <name>{name}</name>\n'
            f'      <pose>{x:.2f} {y:.2f} 0.01 0 0 0</pose>\n'
            f'    </include>\n')


def deterministic_jitter(i: int, span: float) -> float:
    """Repeatable pseudo-jitter in [-span, span] (no RNG — keeps output stable)."""
    return (((i * 2654435761) % 1000) / 1000.0 * 2.0 - 1.0) * span


def build() -> str:
    header = """<?xml version="1.0" encoding="UTF-8"?>
<sdf version="1.9">
  <!-- Skyforge 'forest clearing' stage. Generated by runtime/worlds/make_forest_stage.py.
       Named 'default' on purpose: keeps DART@100Hz physics and all hardcoded
       /world/default/ runtime paths (t1 remove, t2 GUI poll, LED services) working.
       Trees are VISUAL-ONLY (no collision) so they cannot crash a drone. -->
  <world name="default">
    <physics type="dart">
      <max_step_size>0.004</max_step_size>
      <real_time_factor>1.0</real_time_factor>
      <real_time_update_rate>100</real_time_update_rate>
    </physics>
    <gravity>0 0 -9.8</gravity>
    <magnetic_field>6e-06 2.3e-05 -4.2e-05</magnetic_field>
    <atmosphere type="adiabatic"/>
    <scene>
      <grid>false</grid>
      <ambient>0.5 0.5 0.5 1</ambient>
      <background>0.53 0.81 0.92 1</background>
      <shadows>false</shadows>
    </scene>
    <model name="ground_plane">
      <static>true</static>
      <link name="link">
        <collision name="collision">
          <geometry>
            <plane><normal>0 0 1</normal><size>1 1</size></plane>
          </geometry>
          <surface><friction><ode/></friction><bounce/><contact/></surface>
        </collision>
        <visual name="visual">
          <geometry>
            <plane><normal>0 0 1</normal><size>500 500</size></plane>
          </geometry>
          <material>
            <ambient>0.35 0.42 0.3 1</ambient>
            <diffuse>0.35 0.42 0.3 1</diffuse>
            <specular>0.1 0.1 0.1 1</specular>
          </material>
        </visual>
        <pose>0 0 0 0 -0 0</pose>
        <inertial>
          <pose>0 0 0 0 -0 0</pose>
          <mass>1</mass>
          <inertia><ixx>1</ixx><ixy>0</ixy><ixz>0</ixz><iyy>1</iyy><iyz>0</iyz><izz>1</izz></inertia>
        </inertial>
        <enable_wind>false</enable_wind>
      </link>
      <pose>0 0 0 0 -0 0</pose>
      <self_collide>false</self_collide>
    </model>
    <light name="sunUTC" type="directional">
      <pose>0 0 500 0 -0 0</pose>
      <cast_shadows>false</cast_shadows>
      <intensity>1</intensity>
      <direction>0.001 0.625 -0.78</direction>
      <diffuse>0.904 0.904 0.904 1</diffuse>
      <specular>0.271 0.271 0.271 1</specular>
      <attenuation><range>2000</range><linear>0</linear><constant>1</constant><quadratic>0</quadratic></attenuation>
      <spot><inner_angle>0</inner_angle><outer_angle>0</outer_angle><falloff>0</falloff></spot>
    </light>
    <spherical_coordinates>
      <surface_model>EARTH_WGS84</surface_model>
      <world_frame_orientation>ENU</world_frame_orientation>
      <latitude_deg>47.397971057728974</latitude_deg>
      <longitude_deg> 8.546163739800146</longitude_deg>
      <elevation>0</elevation>
    </spherical_coordinates>
"""
    parts = [header]

    # ── Grass floor: 3x3 of 15 m tiles biased into the +x+y show quadrant. ──────
    parts.append("    <!-- Grass floor over the show area -->\n")
    gi = 0
    for gx in (-7.5, 7.5, 22.5):
        for gy in (-7.5, 7.5, 22.5):
            parts.append(grass_tile(f"grass_{gi}", gx, gy))
            gi += 1

    # ── Tree backdrop: two concentric rings of visual-only trees. The outer ring
    #    is phase-offset by half a step so it fills the gaps of the inner ring. ──
    parts.append("    <!-- Visual-only tree backdrop (no collision) ringing the stage -->\n")
    ti = 0
    rings = (
        (N_INNER, R_INNER, 0.0),
        (N_OUTER, R_OUTER, math.pi / N_OUTER),
    )
    for count, radius, phase in rings:
        for k in range(count):
            ang = phase + 2 * math.pi * k / count
            r = radius + deterministic_jitter(ti * 7 + 3, 2.5)
            x = r * math.cos(ang)
            y = r * math.sin(ang)
            kind = "oak" if (ti % 2 == 0) else "pine"
            yaw = deterministic_jitter(ti * 13 + 1, math.pi)
            scale = 1.0 + deterministic_jitter(ti * 5 + 2, 0.25)
            parts.append(tree_model(f"tree_{ti}", kind, x, y, yaw, scale))
            ti += 1

    parts.append("  </world>\n</sdf>\n")
    return "".join(parts)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "forest_stage.sdf"
    with open(out, "w") as f:
        f.write(build())
    print(f"wrote {out}")
