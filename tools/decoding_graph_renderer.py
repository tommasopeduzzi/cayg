#!/usr/bin/env python3
"""
3D Graph Visualizer with Performance Improvements, Error Overlay, Cluster Growth,
Edge/Edge Label Visibility Options, and Single Layer View with Always-Visible Layer Controls

Usage:
    python graph_visualizer_3d_perf.py <graph_file> <errors_file> [--clusters clusters_directory]

File formats:
---------------
Graph file:
  Each line contains an edge in the format:
      node1, node2, edge
  Each node (and the edge label) is encoded as "type-round-id" where:
      For nodes:
         "0" -> Ancilla
         "1" -> Virtual
      For edges:
         "0" -> Measurement
         "1" -> Normal

Errors file:
  Each line is an edge id (encoded as above) that should be highlighted as an error.

Cluster files (optional):
  If the argument --clusters is provided, the directory should contain files named
  like "cluster_0.txt", "cluster_1.txt", etc. Each such file lists only the edges that are part of a cluster.
  Each line in a cluster file has the format:
      {type}-{round}-{id}, {clusterid}

3D Node Layout Logic:
----------------------
  rounds = max([node_round for each node]) + 1
  distance = sqrt( count( edge where both endpoints have round==0 ) )
  nodes_per_layer = ceil(distance / 2)

  For each node (decoded as (type, round, id)):
    - Color: "red" if type == "Ancilla", otherwise "blue"
    - If type == "Virtual":
          if id < nodes_per_layer:
              x = id - 0.5
              y = 0
          else:
              x = id % nodes_per_layer
              y = distance
          z = (rounds - 1) / 2
    - Otherwise (Ancilla):
          x = (id % nodes_per_layer) - ((id // nodes_per_layer) % 2) * 0.5
          y = (id // nodes_per_layer) + 1
          z = round
    Finally, multiply x and y by 3.

Cluster background:
  When decoding steps are shown, a translucent halo is drawn behind each cluster
  (its edges redrawn with a fat, round-capped stroke so they melt into one
  smooth blob) in that cluster's colour. The halo colour can be overridden to
  green for chosen decoding steps (e.g. once a cluster becomes neutral) by
  editing GraphVisualizer3D.neutral_clusters, calling set_cluster_neutral(step,
  cluster_id), or placing a "neutral_clusters.txt" file in the steps directory
  with lines of the form "step, cluster_id" (or "step, all"). Tune the look with
  cluster_halo_width and cluster_background_alpha.

Additional Option:
  A "Single Layer" mode will hide all Ancilla nodes not in a selected layer
  (but Virtual nodes are always rendered). In this mode the view’s z‑limits are adjusted
  so that the displayed layer appears at its actual height. Also, Virtual nodes are forced
  to be rendered at the selected layer's height.

Performance improvements include:
  - Caching computed layout in dictionaries and using NumPy arrays.
  - Grouping drawing into a 3D scatter and Line3DCollections.
  - Debouncing rapid update calls.
  - Manually setting axis limits to avoid autoscaling issues.
  - Overriding node color alpha uniformly.
  - Separate toggles for edge and edge label visibility.
  - When "Edges" is deselected, error edges remain visible if "Errors" is enabled.
  - A concise layer control UI (current layer label with plus and minus buttons) that is always visible.
  - A Reset View button that sets the camera to a top-down view.
"""

import argparse
import math
import os
import re
import sys
import time

import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
from matplotlib.colors import to_rgba
from matplotlib.widgets import CheckButtons, Slider, Button
from mpl_toolkits.mplot3d.art3d import Line3DCollection, Poly3DCollection
from PIL import Image, ImageDraw, ImageFont


class _UniformAlphaFilter:
    """Agg post-processing filter: render the artist opaque, then knock the
    whole rasterised result down to a single alpha. Overlapping strokes inside
    one artist stop compounding, so a fat multi-segment halo reads as one flat
    translucent blob instead of a patchwork of darker overlaps.

    Only takes effect with an Agg-based backend (TkAgg, the Agg used by
    savefig, ...); with others the artist just draws at its own colour alpha.
    """

    def __init__(self, alpha):
        self.alpha = float(alpha)

    def __call__(self, im, dpi):
        out = np.array(im, copy=True)
        out[..., 3] *= self.alpha
        return out, 0, 0


def debounce(wait):
    def decorator(fn):
        last_call = [0]

        def debounced(*args, **kwargs):
            now = time.time()
            if now - last_call[0] > wait:
                last_call[0] = now
                return fn(*args, **kwargs)

        return debounced

    return decorator


class GraphVisualizer3D:
    def __init__(self, graph_file, errors_file, step_dir=None, corrections_file=None):
        self.graph_file = graph_file
        self.errors_file = errors_file
        self.step_dir = step_dir
        self.corrections_file = corrections_file

        # Data structures for the graph.
        self.code = "rotated_surface_code"
        self.G = nx.MultiGraph()
        self.error_edges = set()
        self.correction_edges = set()
        # Maps a correction edge label to the decoding step at which it arrived.
        self.correction_arrival_step = {}
        self.step_data = {}
        self.decoding_steps = []
        self.current_decoding_step = None

        # Toggle flags.
        self.show_nodes = True
        self.show_node_labels = False
        self.show_edges = False
        self.show_edge_labels = False
        self.show_errors = True
        self.show_corrections = True
        self.show_steps = True if step_dir else False
        self.single_layer_enabled = False
        self.selected_layer = 0
        # When True, every round is drawn at full opacity (no per-step dimming).
        self.all_rounds_active = False
        # When set, the "current round" polygon is drawn at this round instead of
        # the one logged for the current decoding step (used by the results image
        # to pin it at max height).
        self._forced_step_round = None
        
        # Colors 
        self.node_color_palette = {
            "Ancilla": "black",
            "Virtual": "blue",
            "Marked": "red"
        }
        self.edge_colors_palette = {
            "normal": "black",
            "error": "red",
            "correction": "#32CD32",  # LimeGreen
            "cluster": [
                # Different shades of blue that still stand out against a white background
                "#00D5FF",  
                "#0059FF",  
                "#00BFFF", 
                "#4000FF",  
                "#1EFFFF",
                "#4C42FF",
            ]
        }
        self.current_round_color = 'lightgray'

        # Translucent halo drawn behind each cluster: the cluster edges redrawn
        # with a fat round-capped stroke, so overlapping/adjacent edges melt into
        # one smooth blob (the "marching-cubes" look, cheaply). Tinted with the
        # cluster colour, except for decoding steps listed in ``neutral_clusters``
        # which force ``neutral_cluster_color`` (flagging neutral clusters).
        self.cluster_background_alpha = 0.25
        self.cluster_halo_width = 15.0        # halo stroke width in points
        self.neutral_cluster_color = "#00E676"  # bright green
        # {decoding_step: {cluster_id, ...}}; map a step to "all" (or True) to
        # mark every cluster neutral at that step. Populate it by editing this
        # dict, calling set_cluster_neutral(), or dropping a plain-text
        # "neutral_clusters.txt" into step_dir (lines "step, cluster_id" or
        # "step, all").
        # self.neutral_clusters = {
        #     2: {4001},
        #     3: {4001},
        #     4: {4001, 2003},
        # }
        self.neutral_clusters = {
            18: {1002},
            23: {4001}
        }

        # Cached computed layout: node -> (x,y,z) and node colors.
        self.pos = {}
        self.node_colors = {}
        self.node_list = []
        self.marked_nodes = set()

        # Artist handles.
        self.node_scatter = None
        self.normal_edge_collection = None
        self.error_edge_collection = None
        self.correction_edge_collection = None
        self.cluster_edge_collections = None
        self.cluster_background_polys = []
        self.node_texts = []
        self.edge_texts = []  # For edge labels.

        # For debouncing updates.
        self._draw_timer = None

        # Create figure and 3D axis.
        self.fig = plt.figure(figsize=(8, 6))
        self.ax = self.fig.add_subplot(111, projection='3d', computed_zorder=False)
        plt.subplots_adjust(left=0.3, bottom=0.15)

        # Load files, compute layout, and create artists.
        self.load_graph()
        self.load_errors()
        self.load_corrections()
        self.load_steps()
        self.compute_layout()
        self.create_artists()
        self.draw_graph()

    def parse_node_str(self, node_str):
        parts = node_str.split('-')
        if len(parts) != 3:
            raise ValueError(f"Invalid node string format: {node_str}")
        type_code = parts[0]
        node_type = "Ancilla" if type_code == "0" else "Virtual" if type_code == "1" else type_code
        if node_type == "Ancilla" and node_str in self.marked_nodes:
            node_type = "Marked"
        try:
            round_ = int(parts[1])
            id_ = int(parts[2])
        except ValueError:
            raise ValueError(f"Non-integer round or id in: {node_str}")
        return (node_type, round_, id_)

    def parse_edge_str(self, edge_str):
        parts = edge_str.split('-')
        if len(parts) != 3:
            raise ValueError(f"Invalid edge string format: {edge_str}")
        code = parts[0]
        etype = "Measurement" if code == "0" else "Normal" if code == "1" else code
        try:
            round_ = int(parts[1])
            id_ = int(parts[2])
        except ValueError:
            raise ValueError(f"Non-integer round or id in: {edge_str}")
        return (etype, round_, id_)

    def load_graph(self):
        try:
            with open(self.graph_file, 'r') as f:
                # If first line is not of type "...,...,...", pop it off
                lines = f.readlines()
                if lines and not re.match(r'^[^,]+,[^,]+,[^,]+$', lines[0].strip()):
                    self.code = lines[0].strip()
                    lines = lines[1:]
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split(',')
                    if len(parts) != 3:
                        continue
                    node1 = parts[0].strip()
                    node2 = parts[1].strip()
                    edge_label = parts[2].strip()
                    self.G.add_node(node1)
                    self.G.add_node(node2)
                    self.G.add_edge(node1, node2, label=edge_label) 
        except Exception as e:
            print(f"Error reading graph file: {e}")
            sys.exit(1)
        self.node_list = list(self.G.nodes())

    def load_errors(self):
        try:
            with open(self.errors_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    self.error_edges.add(line)
        except Exception as e:
            print(f"Error reading errors file: {e}")
            sys.exit(1)
        for u, v, data in self.G.edges(data=True):
            if data.get('label', '') in self.error_edges:
                if u in self.marked_nodes:
                    self.marked_nodes.remove(u)
                else:
                    self.marked_nodes.add(u)
                if v in self.marked_nodes:
                    self.marked_nodes.remove(v)
                else:
                    self.marked_nodes.add(v)

    def load_corrections(self):
        if self.corrections_file is None:
            return
        try:
            with open(self.corrections_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    # Each line is "edge_id" or "edge_id, arrival_step".
                    parts = [p.strip() for p in line.split(',')]
                    edge_id = parts[0]
                    self.correction_edges.add(edge_id)
                    if len(parts) > 1:
                        try:
                            self.correction_arrival_step[edge_id] = int(parts[1])
                        except ValueError:
                            self.correction_arrival_step[edge_id] = -1
                    else:
                        self.correction_arrival_step[edge_id] = -1
        except Exception as e:
            print(f"Error reading corrections file: {e}")
            sys.exit(1)

    def load_steps(self):
        print(self.step_dir)
        if self.step_dir is None:
            return
        try:
            files = [f for f in os.listdir(self.step_dir) if f.endswith('.txt')]
        except Exception as e:
            print(f"Error reading steps directory: {e}")
            sys.exit(1)
        steps = []
        for filename in files:
            re_match = re.search(r'decoding_step_(\d+)\.txt', filename)
            step = int(re_match.group(1)) if re_match else None
            if step is not None and step >= 0:
                steps.append(step)
        steps.sort()
        self.decoding_steps = steps
        for step in steps:
            step_file = os.path.join(self.step_dir, f"decoding_step_{step}.txt")
            step_round = -1
            step_clusters = []
            try:
                with open(step_file, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith('current_round='):
                            step_round = int(line.split('=')[1].strip())
                            continue
                        parts = line.split(',')
                        if len(parts) != 4:
                            continue
                        edge_id = parts[0].strip()
                        tree_node_id = parts[1].strip()
                        growth = parts[2].strip()
                        cluster_id = parts[3].strip()
                        step_clusters.append({
                            'edge_id': edge_id,
                            'tree_node_id': tree_node_id,
                            'growth': growth,
                            'cluster_id': cluster_id
                        })
                self.step_data[step] = (step_round, step_clusters)
            except Exception as e:
                print(f"Error reading decoding step file {step_file}: {e}")
        neutral_file = os.path.join(self.step_dir, "neutral_clusters.txt")
        if os.path.exists(neutral_file):
            self._load_neutral_clusters(neutral_file)
        if steps:
            self.current_decoding_step = steps[0]

    def _load_neutral_clusters(self, path):
        """Load neutral-cluster overrides from a plain-text file.

        Each non-empty, non-comment line is either "step, cluster_id" (mark that
        one cluster neutral at that decoding step) or "step, all" (mark every
        cluster neutral at that step).
        """
        try:
            with open(path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    parts = [p.strip() for p in line.split(',')]
                    if len(parts) < 2:
                        continue
                    try:
                        step = int(parts[0])
                    except ValueError:
                        continue
                    if parts[1].lower() == 'all':
                        self.neutral_clusters[step] = 'all'
                    else:
                        try:
                            self.set_cluster_neutral(step, int(parts[1]))
                        except ValueError:
                            continue
        except Exception as e:
            print(f"Error reading neutral clusters file {path}: {e}")

    def set_cluster_neutral(self, step, cluster_id, neutral=True):
        """Override the halo colour of ``cluster_id`` at decoding ``step`` to the
        neutral colour (green) instead of the cluster's own colour."""
        current = self.neutral_clusters.get(step)
        if current in ('all', True):
            return
        ids = current if isinstance(current, set) else set()
        if neutral:
            ids.add(cluster_id)
        else:
            ids.discard(cluster_id)
        self.neutral_clusters[step] = ids

    def _is_cluster_neutral(self, step, cluster_id):
        entry = self.neutral_clusters.get(step, self.neutral_clusters.get('all'))
        if entry in ('all', True):
            return True
        return bool(entry) and cluster_id in entry

    def compute_layout(self):
        """Compute positions and colors for all nodes using the provided logic."""
        parsed_nodes = []
        for node in self.node_list:
            try:
                parsed_nodes.append(self.parse_node_str(node))
            except Exception as e:
                print(f"Skipping node {node}: {e}")
        if not parsed_nodes:
            print("No valid nodes found.")
            return
        max_round = max(r for (_, r, _) in parsed_nodes)
        rounds = max_round + 1
        self.total_layers = rounds
        # Count edges with both endpoints in round 0.
        virtual_edge_count = 0
        for u, v in self.G.edges():
            try:
                _, r1, _ = self.parse_node_str(u)
                _, r2, _ = self.parse_node_str(v)
                if r1 == 0 and r2 == 0:
                    virtual_edge_count += 1
            except Exception:
                continue
        
        if self.code == "rotated_surface_code":
            distance = virtual_edge_count ** 0.5
            nodes_per_layer = math.floor(distance / 2) + 1  if distance > 0 else 1
            for node in self.node_list:
                try:
                    t, r, i = self.parse_node_str(node)
                except Exception:
                    continue
                self.node_colors[node] = self.node_color_palette[t]
                if t == "Virtual":
                    if i == 0:
                        x = nodes_per_layer / 2 - 0.5
                        y = 0
                    else:
                        x = nodes_per_layer / 2 - 0.5
                        y = distance - 0.5
                    z = (rounds - 1) / 2
                else:
                    x = (i % nodes_per_layer) - ((i // nodes_per_layer) % 2) * 0.5
                    y = (i // nodes_per_layer) + 1
                    z = r
                self.pos[node] = (x * 3, y * 3, z)
        elif self.code == "repetition_code":
            distance = len(parsed_nodes) ** 0.5
            for node in self.node_list:
                try:
                    t, r, i = self.parse_node_str(node)
                except Exception:
                    continue
                self.node_colors[node] = self.node_color_palette[t]
                if t == "Virtual":
                    y = -2 if i == 0 else distance + 1
                    x = 1
                    z = (rounds - 1) / 2
                else:
                    y = i
                    x = 1
                    z = r
                self.pos[node] = (x * 3, y * 3, z)
        else: 
            print(f"Unknown code type: {self.code}. Cannot compute layout.")
            sys.exit(1)

    def create_artists(self):
        """Create initial scatter and edge collections using dummy empty arrays."""
        self.node_scatter = self.ax.scatter([], [], [], s=100, alpha=0.8)
        dummy_seg = np.empty((0, 2, 3))
        self.normal_edge_collection = Line3DCollection(dummy_seg, colors=self.edge_colors_palette["normal"], linewidths=1, zorder=-2)
        self.ax.add_collection3d(self.normal_edge_collection)
        self.error_edge_collection = Line3DCollection(dummy_seg, colors=self.edge_colors_palette["error"], linewidths=2.7, )
        self.ax.add_collection3d(self.error_edge_collection)
        self.correction_edge_collection = Line3DCollection(dummy_seg, colors=self.edge_colors_palette["correction"], linewidths=2.5)
        self.ax.add_collection3d(self.correction_edge_collection)
        self.cluster_edge_collections = []
        self.current_round_polys = []
        self.cluster_background_polys = []
        self.node_texts = []
        self.edge_texts = []

    def schedule_draw(self):
        """Debounce redraws by scheduling one after a short delay."""
        if self._draw_timer is not None:
            self._draw_timer.stop()
        self._draw_timer = self.fig.canvas.new_timer(interval=50)
        self._draw_timer.add_callback(self.draw_graph)
        self._draw_timer.start()

    def draw_graph(self):
        """
        Update node and edge positions using cached collections.
        In single-layer mode, Virtual nodes are drawn at the selected layer's height.
        All node colors are converted to RGBA with a fixed alpha.
        Edge visibility and edge label visibility are handled as follows:
          - Normal edges are shown only if "Edges" is enabled.
          - Error edges are drawn if "Errors" is enabled.
          - Correction edges are drawn if "Corrections" is enabled.
          - Edge labels are drawn if "Edge Labels" is enabled (independent of the "Edges" toggle).
        """
        # Determine visible nodes.
        if self.single_layer_enabled:
            visible_nodes = []
            for node in self.node_list:
                try:
                    t, r, _ = self.parse_node_str(node)
                except Exception:
                    continue
                if t == "Virtual" or (t == "Ancilla" and r == self.selected_layer):
                    visible_nodes.append(node)
        else:
            visible_nodes = list(self.node_list)

        # Build local dictionary of positions for drawing.
        # In single-layer mode, force Virtual nodes to have z equal to selected_layer.
        draw_pos = {}
        for node in visible_nodes:
            if node in self.pos:
                x, y, z = self.pos[node]
                try:
                    t, r, _ = self.parse_node_str(node)
                except Exception:
                    t = None
                if self.single_layer_enabled and t == "Virtual":
                    z = self.selected_layer
                draw_pos[node] = (x, y, z)

        # Update node scatter.
        xs, ys, zs, colors = [], [], [], []
        if self.show_nodes:
            for node in visible_nodes:
                if node in draw_pos:
                    x, y, z = draw_pos[node]
                    xs.append(x)
                    ys.append(y)
                    zs.append(z)
                    _, round, _= self.parse_node_str(node)
                    if self.all_rounds_active:
                        alpha = 0.8
                    else:
                        alpha = 0.8 if round <= self.step_data.get(self.current_decoding_step, (-1,[]))[0] else 0.3
                    colors.append(to_rgba(self.node_colors[node], alpha=alpha))
        if self.node_scatter is not None:
            self.node_scatter.remove()
            self.node_scatter = self.ax.scatter(xs, ys, zs, c=colors, s=100, zorder=10)
            self.node_scatter.set_visible(self.show_nodes)
        else:
            self.node_scatter = self.ax.scatter(xs, ys, zs, c=colors, s=100, alpha=0.8, zorder=10)
            self.node_scatter.set_visible(self.show_nodes)
        # Keep nodes painted on top of every edge / halo / round polygon.
        self.node_scatter.set_zorder(10)

        # Update node labels.
        for txt in self.node_texts:
            txt.remove()
        self.node_texts = []
        if self.show_node_labels and self.show_nodes:
            for node in visible_nodes:
                if node in draw_pos:
                    x, y, z = draw_pos[node]
                    txt = self.ax.text(x, y, z, node, size=8)
                    self.node_texts.append(txt)

        # Remove previous edge labels.
        for txt in self.edge_texts:
            txt.remove()
        self.edge_texts = []

        # Prepare edge segments.
        all_segments = []
        error_segments = []
        corrections_segment = []
        for u, v, data in self.G.edges(data=True):
            if u not in visible_nodes or v not in visible_nodes:
                continue
            seg = [draw_pos[u], draw_pos[v]]
            all_segments.append(seg)
            if data.get('label', '') in self.error_edges:
                error_segments.append(seg)
                if u in self.marked_nodes:
                    self.marked_nodes.remove(u)
                else:
                    self.marked_nodes.add(u)
                if v in self.marked_nodes:
                    self.marked_nodes.remove(v)
                else:
                    self.marked_nodes.add(v)
            if data.get('label', '') in self.correction_edges:
                # Only show corrections that have arrived up to the current decoding step.
                if self.step_dir and self.current_decoding_step is not None:
                    arrival_step = self.correction_arrival_step.get(data.get('label', ''), -1)
                    if arrival_step <= self.current_decoding_step:
                        corrections_segment.append(seg)
                else:
                    corrections_segment.append(seg)
            if not self.show_edge_labels:
                continue
            # Check for parallel edges and space labels if needed
            edges_between = [e for e in self.G.edges(data=True) if (e[0] == u and e[1] == v) or (e[0] == v and e[1] == u)]
            num_parallel = len(edges_between)
            # Find index of current edge among parallel edges
            edge_idx = [i for i, e in enumerate(edges_between) if e[2].get('label', '') == data.get('label', '')]
            idx = edge_idx[0] + 1 if edge_idx else 1
            xm = draw_pos[u][0] + (draw_pos[v][0] - draw_pos[u][0]) / (num_parallel + 1) * idx
            ym = draw_pos[u][1] + (draw_pos[v][1] - draw_pos[u][1]) / (num_parallel + 1) * idx
            zm = draw_pos[u][2] + (draw_pos[v][2] - draw_pos[u][2]) / (num_parallel + 1) * idx
            label = data.get('label', '')
            try:
                etype, eround, eid = self.parse_edge_str(label)
                display_label = f"{eround}-{eid}"
            except Exception:
                display_label = label
            txt = self.ax.text(xm, ym, zm, display_label, size=10, color='black')
            self.edge_texts.append(txt)

        # Update normal edges: show only if "Edges" is enabled.
        if self.show_edges:
            self.normal_edge_collection.set_segments(all_segments)
        else:
            self.normal_edge_collection.set_segments(np.empty((0, 2, 3)))

        # Update error edges independently: drawn if "Errors" is enabled.
        if self.show_errors:
            self.error_edge_collection.set_segments(error_segments)
            self.error_edge_collection.set_color(self.edge_colors_palette["error"])
            self.error_edge_collection.set_linewidth(1.5)
            self.error_edge_collection.set_zorder(9)
        else:
            self.error_edge_collection.set_segments(np.empty((0, 2, 3)))

        # Update correction edges.
        if self.show_corrections:
            self.correction_edge_collection.set_segments(corrections_segment)
            self.correction_edge_collection.set_color(self.edge_colors_palette["correction"])
            self.correction_edge_collection.set_linewidth(3.5)
        else:
            self.correction_edge_collection.set_segments(np.empty((0, 2, 3)))
        
        # Clear any previously drawn round polygons / cluster edges. These are
        # recreated below only when decoding steps are shown, so clearing here
        # ensures they disappear when steps are toggled off.
        for current_round_poly in self.current_round_polys:
            current_round_poly.remove()
        self.current_round_polys = []
        for poly in self.cluster_background_polys:
            poly.remove()
        self.cluster_background_polys = []
        for collection in self.cluster_edge_collections:
            collection.remove()
        self.cluster_edge_collections = []

        # Prepare cluster edges.
        if self.step_dir and self.show_steps:
            step_round, step_clusters = self.step_data.get(self.current_decoding_step, (-1,[]))
            poly_round = self._forced_step_round if self._forced_step_round is not None else step_round
            if poly_round >= 0:
                # Draw a transparent polygon at the current round height.
                z_height = poly_round+0.25
                x_vals = [min(xs) - 2, max(xs) + 2, max(xs) + 2, min(xs) - 2]+[min(xs) - 2, max(xs) + 2, max(xs) + 2, min(xs) - 2]
                y_vals = [min(ys) - 2, min(ys) - 2, max(ys) + 2, max(ys) + 2]+[min(ys) - 2, min(ys) - 2, max(ys) + 2, max(ys) + 2]
                z_vals = [-0.5,-0.5,-0.5,-0.5] + [z_height] * 4
                verts = [list(zip(x_vals, y_vals, z_vals))]
                faces = [
                    [0, 1, 2, 3],  # bottom (z = 0)
                    [4, 5, 6, 7],  # top (z = 1)
                    [0, 1, 5, 4],  # front (y = 0)
                    [2, 3, 7, 6],  # back (y = 1)
                    [1, 2, 6, 5],  # right (x = 1)
                    [0, 3, 7, 4],  # left (x = 0)
                ]
                for face in faces:
                    poly_verts = [verts[0][i] for i in face]
                    poly = Poly3DCollection([poly_verts], alpha=0.2, facecolor=self.current_round_color, zorder=-1)
                    self.ax.add_collection3d(poly)
                    self.current_round_polys.append(poly)

            cluster_segments = {}
            for cluster_entry in step_clusters:
                edge_id = cluster_entry['edge_id']
                tree_node_id = cluster_entry['tree_node_id']
                # Clamp to [0, 1]: a cluster grows along an edge but never past the
                # node it connects to. Logged growth can exceed the edge weight when
                # a boundary edge keeps accumulating growth without fusing, which
                # would otherwise draw a segment poking out of the grid.
                growth = max(0.0, min(1.0, float(cluster_entry['growth'])))
                cluster_id = int(cluster_entry['cluster_id'])
                # Find the edge in the graph matching edge_id
                for u, v, data in self.G.edges(data=True):
                    if u not in visible_nodes or v not in visible_nodes:
                        continue
                    if data.get('label', '') == edge_id:
                        # Find which node matches tree_node_id
                        if u == tree_node_id:
                            start = draw_pos[u]
                            end = draw_pos[v]
                        elif v == tree_node_id:
                            start = draw_pos[v]
                            end = draw_pos[u]
                        else:
                            # fallback: use u as start
                            start = draw_pos[u]
                            end = draw_pos[v]
                        # Compute the segment according to growth
                        seg = ([
                            start,
                            (
                                start[0] + (end[0] - start[0]) * growth,
                                start[1] + (end[1] - start[1]) * growth,
                                start[2] + (end[2] - start[2]) * growth
                            )
                        ])
                        if cluster_id not in cluster_segments:
                            cluster_segments[cluster_id] = []
                        cluster_segments[cluster_id].append(seg)
                        break

            for cluster_id, segments in cluster_segments.items():
                cluster_color = self.edge_colors_palette["cluster"][cluster_id % len(self.edge_colors_palette["cluster"])]
                if self._is_cluster_neutral(self.current_decoding_step, cluster_id):
                    halo_color = self.neutral_cluster_color
                else:
                    halo_color = cluster_color

                # Halo: the same segments drawn with a fat, round-capped stroke.
                # Round caps/joins make adjacent and overlapping edges merge into
                # one smooth blob behind the crisp cluster lines. Drawn opaque
                # and flattened to a single alpha by the agg filter so the
                # overlaps don't compound into darker patches.
                halo = Line3DCollection(
                    segments,
                    colors=to_rgba(halo_color, 1.0),
                    linewidths=self.cluster_halo_width,
                    capstyle='round', joinstyle='round', zorder=-3,
                )
                halo.set_agg_filter(_UniformAlphaFilter(self.cluster_background_alpha))
                self.ax.add_collection3d(halo)
                self.cluster_background_polys.append(halo)

                collection = Line3DCollection(segments, colors=cluster_color, linewidths=3.5)
                self.cluster_edge_collections.append(collection)
                self.ax.add_collection3d(collection)

        # Manually set x- and y-axis limits based on visible nodes.
        if xs:
            self.ax.set_xlim(min(xs) - 1, max(xs) + 1)
        if ys:
            self.ax.set_ylim(min(ys) - 1, max(ys) + 1)
        # Set z-axis limits.
        if self.single_layer_enabled:
            self.ax.set_zlim(self.selected_layer - 1, self.selected_layer + 1)
        else:
            if zs:
                self.ax.set_zlim(min(zs) - 1, max(zs) + 1)

        # Remove axes.
        self.ax.set_axis_off()
        # Side view by default.
        self.fig.canvas.draw_idle()

    def toggle_visibility(self, label):
        if label == 'Nodes':
            self.show_nodes = not self.show_nodes
        elif label == 'Node Labels':
            self.show_node_labels = not self.show_node_labels
        elif label == 'Edges':
            self.show_edges = not self.show_edges
        elif label == 'Edge Labels':
            self.show_edge_labels = not self.show_edge_labels
        elif label == 'Errors':
            self.show_errors = not self.show_errors
        elif label == 'Corrections':
            self.show_corrections = not self.show_corrections
        elif label == 'Clusters' and self.step_dir:
            self.show_steps = not self.show_steps
        elif label == 'Single Layer':
            self.single_layer_enabled = not self.single_layer_enabled
        self.schedule_draw()

    def update_cluster_step(self, val):
        self.current_decoding_step = int(val)
        self.schedule_draw()

    def update_selected_layer(self, label):
        try:
            self.selected_layer = int(label)
        except ValueError:
            self.selected_layer = 0
        self.schedule_draw()

    def top_view(self):
        """Reset the camera view to a top-down perspective."""
        self.ax.view_init(elev=90, azim=-90)
        self.fig.canvas.draw_idle()

    def side_view(self):
        """Reset the camera view to a top-down perspective."""
        self.ax.view_init(elev=0, azim=0)
        self.fig.canvas.draw_idle()

    def save_errors_image(self, output_path):
        """Render just the errors on the full graph (no clusters, all rounds active)."""
        # Remember the current view configuration so this export is non-destructive.
        saved = (self.show_nodes, self.show_node_labels, self.show_edges, self.show_edge_labels,
                 self.show_errors, self.show_corrections, self.show_steps, self.all_rounds_active)

        self.show_nodes = True
        self.show_node_labels = False
        self.show_edges = False
        self.show_edge_labels = False
        self.show_errors = True
        self.show_corrections = False
        self.show_steps = False
        self.all_rounds_active = True

        self.draw_graph()
        self.ax.view_init(elev=0, azim=0)
        self.fig.canvas.draw()
        self.fig.savefig(output_path, dpi=300, bbox_inches='tight', transparent=True)
        print(f"[INFO] Saved errors image to {output_path}")

        # Restore the previous configuration.
        (self.show_nodes, self.show_node_labels, self.show_edges, self.show_edge_labels,
         self.show_errors, self.show_corrections, self.show_steps, self.all_rounds_active) = saved
        self.draw_graph()

    def save_results_image(self, output_path, round_=None):
        """Render the final result: all errors and all corrections, with the
        current-round polygon pinned at its maximum height.

        :param round_: round to pin the polygon at (default: the highest round
            seen across all decoding steps).
        """
        saved = (self.show_nodes, self.show_node_labels, self.show_edges, self.show_edge_labels,
                 self.show_errors, self.show_corrections, self.show_steps, self.all_rounds_active,
                 self.current_decoding_step, self._forced_step_round)

        if round_ is None:
            round_ = max((r for r, _ in self.step_data.values()), default=self.total_layers - 1)

        self.show_nodes = True
        self.show_node_labels = False
        self.show_edges = False
        self.show_edge_labels = False
        self.show_errors = True
        self.show_corrections = True
        self.show_steps = True
        self.all_rounds_active = True
        # Jump to the last step so every correction has arrived (they are gated
        # by arrival step) and all clusters have been peeled away.
        if self.decoding_steps:
            self.current_decoding_step = self.decoding_steps[-1]
        self._forced_step_round = round_

        self.draw_graph()
        self.ax.view_init(elev=0, azim=0)
        self.fig.canvas.draw()
        self.fig.savefig(output_path, dpi=300, bbox_inches='tight', transparent=True)
        print(f"[INFO] Saved results image to {output_path}")

        (self.show_nodes, self.show_node_labels, self.show_edges, self.show_edge_labels,
         self.show_errors, self.show_corrections, self.show_steps, self.all_rounds_active,
         self.current_decoding_step, self._forced_step_round) = saved
        self.draw_graph()

    @staticmethod
    def _crop_frames(paths, crop, padding=0):
        """Crop the given image files in place to a single shared box.

        ``crop`` may be:
          - ``False`` -> do nothing;
          - a 4-tuple ``(left, upper, right, lower)`` -> absolute pixel box;
          - ``None`` -> auto-fit the union of every frame's non-transparent
            content, then grow it by ``padding``.
        ``padding`` is either one number (all sides) or a 4-tuple
        ``(left, top, right, bottom)``; positive values add margin, negative
        values eat into the content.
        """
        if crop is False:
            return
        paths = [p for p in paths if p and os.path.exists(p)]
        if not paths:
            return

        if isinstance(crop, (tuple, list)) and len(crop) == 4:
            box = [int(v) for v in crop]
        else:
            if isinstance(padding, (tuple, list)):
                pl, pt, pr, pb = (int(v) for v in padding)
            else:
                pl = pt = pr = pb = int(padding)
            box = None
            for p in paths:
                bbox = Image.open(p).getbbox()  # non-transparent extent
                if bbox is None:
                    continue
                if box is None:
                    box = list(bbox)
                else:
                    box = [min(box[0], bbox[0]), min(box[1], bbox[1]),
                           max(box[2], bbox[2]), max(box[3], bbox[3])]
            if box is None:
                return
            box = [box[0] - pl, box[1] - pt, box[2] + pr, box[3] + pb]

        for p in paths:
            im = Image.open(p)
            clamped = (max(0, box[0]), max(0, box[1]),
                       min(im.width, box[2]), min(im.height, box[3]))
            im.crop(clamped).save(p)
        print(f"[INFO] Cropped {len(paths)} frame(s) to {tuple(box)}")

    def save_clustering_animation(self, animation_dir, make_comic=False, animation_grid_path=None,
                                  crop=None, crop_padding=0):
        """
        Export images for each cluster step to animation_dir.
        Optionally, combine them into a comic-strip style image.

        :param animation_dir: directory to save individual step images
        :param make_comic: if True, generate a single comic-strip image
        :param comic_path: path to save comic-strip image
        :param arrow_size: length of arrows in comic strip
        :param spacing: vertical padding in comic strip
        :param crop: how to trim the exported frames (errors.png, every
            step_*.png and the gif) so there is no dead border around the
            drawing. ``None`` (default) auto-crops every frame to one shared
            box: the union of the rendered (non-transparent) content across all
            frames, i.e. the current-round polygon at its largest. Pass a
            4-tuple ``(left, upper, right, lower)`` in pixels to crop to a fixed
            box instead. Pass ``False`` to disable cropping.
        :param crop_padding: margin added to the auto-crop box - one number for
            all sides, or a 4-tuple ``(left, top, right, bottom)``. Negative
            values crop inward. Ignored when ``crop`` is an absolute box.
        """
        os.makedirs(animation_dir, exist_ok=True)
        print(f"[INFO] Exporting cluster step images to: {animation_dir}")
        exported_files = []

        # Render the errors on the full graph first, so the animation shows the
        # error before the decoding steps.
        error_image_path = os.path.join(animation_dir, "errors.png")
        self.save_errors_image(error_image_path)

        self.show_edges = False
        self.show_edge_labels = False
        self.show_nodes = True
        self.show_steps = True
        self.show_corrections = True
        self.show_errors = True

        # Steps on which to also overlay the errors during the animation
        # (errors otherwise live in errors.png / results.png only).
        show_error_steps = []

        previous_decoding_step = self.decoding_steps[0] if self.decoding_steps else None
        for step in self.decoding_steps:
            self.show_edges = False
            self.show_edge_labels = False
            self.show_nodes = True
            self.show_steps = True

            # Corrections are gated by their arrival step in draw_graph.
            self.show_corrections = True

            self.show_errors = step in show_error_steps
            self.current_decoding_step = step

            self.draw_graph()
            self.ax.view_init(elev=0, azim=0)
            self.fig.canvas.draw()  # update buffer
            output_path = os.path.join(animation_dir, f"step_{step:03d}.png")
            current_decoding_step = self.step_data.get(step)
            if previous_decoding_step is not None and current_decoding_step == previous_decoding_step and not step == self.decoding_steps[-1]:
                print(f"[INFO]  Skipping {output_path} (no changes)")
                continue
            
            self.fig.savefig(output_path, dpi=300, bbox_inches='tight', transparent=True)
            previous_decoding_step = current_decoding_step
            exported_files.append(output_path)
            print(f"[INFO]  Saved {output_path}")

        # Final result frame: all errors + all corrections, current round pinned
        # at max height.
        results_image_path = os.path.join(animation_dir, "results.png")
        self.save_results_image(results_image_path)

        print("[INFO] Export complete.")

        # Trim the dead border off every exported frame, using one shared box so
        # the frames (and the gif built from them) stay aligned.
        self._crop_frames([error_image_path] + exported_files + [results_image_path],
                          crop, crop_padding)

        if not make_comic:
            return

        if animation_grid_path is None:
            animation_grid_path = os.path.join(animation_dir, "animation.png")

        columns = 3
        # Frames are already trimmed by _crop_frames above.
        crop_margin_width = 0
        crop_margin_height = 0
        spacing_x = 20
        spacing_y = 20
        include_start_in_comic = False
        include_final_corrections_in_comic = False
        correction_arrow_color = (255, 0, 0, 255)
        new_syndrome_round_arrow_color = (0, 0, 255, 255)
                
        # Load and crop images
        imgs = []
        for f in exported_files:
            img = Image.open(f).convert("RGBA")
            w, h = img.size
            left = crop_margin_width
            upper = crop_margin_height
            right = max(w - crop_margin_width, left)
            lower = max(h - crop_margin_height, upper)
            img = img.crop((left, upper, right, lower))
            imgs.append(img)
        
        if not include_start_in_comic:
            imgs = imgs[1:]
        if not include_final_corrections_in_comic:
            imgs = imgs[:-1]

        widths, heights = zip(*(i.size for i in imgs))
        max_width = max(widths)
        max_height = max(heights)

        n_images = len(imgs)
        rows = (n_images + columns - 1) // columns

        canvas_width = columns * max_width + (columns - 1) * spacing_x
        canvas_height = rows * max_height + (rows - 1) * spacing_y
        new_img = Image.new('RGBA', (canvas_width, canvas_height), color=(255, 255, 255, 0))
        draw = ImageDraw.Draw(new_img, 'RGBA')

        # Precompute centers for arrow path
        centers = []
        arrows = []
        for row in range(rows):
            y_offset = row * (max_height + spacing_y)
            for col in range(columns):
                idx = row * columns + col
                if idx >= n_images:
                    break
                if row % 2 == 0:
                    x_offset = col * (max_width + spacing_x)
                else:
                    x_offset = canvas_width - (col + 1) * (max_width + spacing_x) + spacing_x
                cx = x_offset + max_width // 2
                cy = y_offset + max_height // 2
                centers.append((cx, cy))
        
        # CAYG label color arrows
        correction_arrows = [7, 11]
        new_syndrome_round_arrows = [0,2,4,8,12]
        
        # UF label color arrows
        #correction_arrows, new_syndrome_round_arrows = [], []
        
        # Arrow margins
        h_margin = 0.05 * max_width
        v_margin = 0.06 * max_height

        for i in range(len(centers) - 1):
            x1, y1 = centers[i]
            x2, y2 = centers[i + 1]

            dx = x2 - x1
            dy = y2 - y1

            # Alternating sign

            # Predominantly horizontal arrow
            if abs(dx) > abs(dy):
                sign = np.sign(dx)

                sx = 15
                sy = -17

                start = (
                    x1 + sign * (max_width // 2 - h_margin) + sx,
                    y1 + sy
                )
                end = (
                    x2 - sign * (max_width // 2 - h_margin) + sx,
                    y2 + sy
                )

            # Predominantly vertical arrow
            else:
                sign = np.sign(dy)

                sx = 7
                sy = -12

                start = (
                    x1 + sx,
                    y1 + sign * (max_height // 2 - v_margin) + sy
                )
                end = (
                    x2 + sx,
                    y2 - sign * (max_height // 2 - v_margin) + sy
                )
            if i in correction_arrows:
                color = correction_arrow_color
            elif i in new_syndrome_round_arrows:
                color = new_syndrome_round_arrow_color
            else:
                color = (0, 0, 0, 255)

            arrows.append((start, end, color))

        # # Draw the faint zig-zag arrow path
        # if len(centers) > 1:
        #     draw.line(centers, fill=arrow_color, width=arrow_width, joint="curve")

        #     # Draw a large arrowhead at the end
        #     end_x, end_y = centers[-1]
        #     head_size = arrow_width * 1.2
        #     draw.polygon(
        #         [
        #             (end_x + head_size*((-1)**(rows-1)), end_y),
        #             (end_x, end_y - head_size*0.7),
        #             (end_x, end_y + head_size*0.7),
        #         ],
        #         fill=arrow_color,
        #     )

        

        # Paste images
        for row in range(rows):
            y_offset = row * (max_height + spacing_y)
            for col in range(columns):
                idx = row * columns + col
                if idx >= n_images:
                    break
                img = imgs[idx]
                if row % 2 == 0:
                    x_offset = col * (max_width + spacing_x)
                else:
                    x_offset = canvas_width - (col + 1) * (max_width + spacing_x) + spacing_x
                new_img.alpha_composite(img, (x_offset, y_offset))
                
        head_size = 28

        for start, end, color in arrows:
            draw.line([start, end], fill=color, width=8)
            
            dx = end[0] - start[0]
            dy = end[1] - start[1]
            length = np.hypot(dx, dy)
            if length == 0:
                continue

            ux = dx / length
            uy = dy / length
            px = -uy  # perpendicular
            py = ux

            # Determine direction case
            if abs(dx) >= abs(dy) and dx >= 0:
                # Left to right
                tip = (end[0] + ux * head_size * 0.3, end[1])
                label_offset = (0, -head_size * 2)
            elif abs(dx) >= abs(dy) and dx < 0:
                # Right to left
                tip = (end[0] + ux * head_size * 0.3, end[1])
                label_offset = (0, -head_size * 2)
            else:
                # Top to bottom (or bottom to top)
                tip = (end[0], end[1] + uy * head_size * 0.3)
                label_offset = (head_size * 2.2, -10)  # offset label to the right of arrow

            base = (
                end[0] - ux * head_size,
                end[1] - uy * head_size,
            )
            left = (
                base[0] + px * head_size * 0.6,
                base[1] + py * head_size * 0.6,
            )
            right = (
                base[0] - px * head_size * 0.6,
                base[1] - py * head_size * 0.6,
            )
            draw.polygon([tip, left, right], fill=color)

            # Label
            label = chr(65 + (arrows.index((start, end, color)) % 26)).lower() + ")"
            font = ImageFont.load_default(size=100)
            mid_x = (start[0] + end[0]) / 2 + label_offset[0]
            mid_y = (start[1] + end[1]) / 2 + label_offset[1]
            draw.text((mid_x, mid_y), label, fill=(0, 0, 0, 255), font=font, anchor="mm")      
                  
        new_img.save(animation_grid_path)
        print(f"[INFO] Comic strip saved to {animation_grid_path}")
        
        # Create gif: errors first, then every step frame, ending on the result.
        gif_path = os.path.join(animation_dir, "animation.gif")
        gif_sources = [error_image_path] + exported_files + [results_image_path]
        raw_frames = [Image.open(f).convert("RGBA") for f in gif_sources]
        max_w = max(im.width for im in raw_frames)
        max_h = max(im.height for im in raw_frames)
        gif_frames = []
        for im in raw_frames:
            canvas = Image.new("RGBA", (max_w, max_h), (255, 255, 255, 255))
            canvas.alpha_composite(im, ((max_w - im.width) // 2, (max_h - im.height) // 2))
            gif_frames.append(canvas)
        gif_frames[0].save(gif_path, save_all=True, append_images=gif_frames[1:],
                           duration=1500, loop=0, disposal=2)
        print(f"[INFO] Animated GIF saved to {gif_path}")



def main():
    parser = argparse.ArgumentParser(
        description="3D Visualize a graph with error overlay, cluster growth, and single layer view."
    )
    parser.add_argument('directory', help='Directory with runs.')
    parser.add_argument('run_id', help='Run to visualize.')
    parser.add_argument('--graph_file', help='Path to the graph file')
    parser.add_argument('--errors_file', help='Path to the errors file')
    parser.add_argument('--decoder', help='Decoder for which to show clustering.')
    parser.add_argument('--steps', help='Directory containing cluster step files', default=None)
    parser.add_argument("--corrections_file", help="File containing corrections by decoder")
    parser.add_argument('--animation', help='If set, exports each cluster step as an image to directory specified or default.')
    parser.add_argument('--crop', default=None,
                        help='Trim exported frames (default: auto-fit to content, 0 px padding). '
                             '"none" disables it. Auto-fit + padding: "N" (all sides), "X,Y" '
                             '(left/right, top/bottom) or "L,T,R,B" per side; negatives crop inward. '
                             '"box:L,T,R,B" is an absolute pixel box instead. Quote it in the shell.')
    args = parser.parse_args()

    crop = None
    crop_padding = 0
    if args.crop is not None:
        raw = args.crop.strip().strip('()[]').replace(' ', '')
        if raw.lower() == 'none':
            crop = False
        elif raw.lower().startswith('box:'):
            crop = tuple(int(v) for v in raw[4:].split(','))
        elif ',' in raw:
            vals = [int(v) for v in raw.split(',')]
            if len(vals) == 2:
                crop_padding = (vals[0], vals[1], vals[0], vals[1])
            elif len(vals) == 4:
                crop_padding = tuple(vals)
            else:
                parser.error('--crop expects "N", "X,Y", "L,T,R,B", "box:L,T,R,B" or "none"')
        else:
            crop_padding = int(raw)

    if not args.graph_file:
        args.graph_file = f"{args.directory}/{args.run_id}/graph.txt"
    if not args.errors_file:
        args.errors_file = f"{args.directory}/{args.run_id}/errors.txt"
    if args.decoder:
        if not args.steps:
            args.steps = f"{args.directory}/{args.run_id}/{args.decoder}"
        if not args.corrections_file:
            args.corrections_file = f"{args.directory}/{args.run_id}/{args.decoder}/corrections.txt"

    visualizer = GraphVisualizer3D(args.graph_file, args.errors_file, step_dir=args.steps,
                                   corrections_file=args.corrections_file)
    # --- Export mode: save images for each cluster step instead of interactive display ---
    if args.animation and args.decoder and args.steps and visualizer.decoding_steps:
        animation_dir = args.animation
        os.makedirs(animation_dir, exist_ok=True)
        visualizer.save_clustering_animation(animation_dir, make_comic=True,
                                             crop=crop, crop_padding=crop_padding)
        sys.exit(0)
        
    # Create check buttons.
    rax = plt.axes([0.01, 0.4, 0.2, 0.35])
    check_labels = ['Nodes', 'Node Labels', 'Edges', 'Edge Labels', 'Errors']
    if args.corrections_file:
        check_labels.append('Corrections')
    if args.steps:
        check_labels.append('Decoding Steps')
    check_labels.append('Single Layer')
    initial = [visualizer.show_nodes, visualizer.show_node_labels,
               visualizer.show_edges, visualizer.show_edge_labels,
               visualizer.show_errors, visualizer.show_corrections]
    if args.steps:
        initial.append(visualizer.show_steps)
    initial.append(visualizer.single_layer_enabled)
    check = CheckButtons(rax, check_labels, initial)
    check.on_clicked(visualizer.toggle_visibility)

    # If clusters are provided, add a slider.
    if args.steps and visualizer.decoding_steps:
        axstep = plt.axes([0.3, 0.1, 0.5, 0.03])
        slider = Slider(axstep, 'Cluster Step',
                        min(visualizer.decoding_steps),
                        max(visualizer.decoding_steps),
                        valinit=min(visualizer.decoding_steps),
                        valstep=1)
        slider.on_changed(visualizer.update_cluster_step)

    # Create concise layer control UI (always visible).
    ax_layer_label = plt.axes([0.01, 0.30, 0.2, 0.04])
    layer_text = ax_layer_label.text(0.5, 0.5, f"Layer: {visualizer.selected_layer}",
                                     transform=ax_layer_label.transAxes, ha="center", va="center")
    ax_layer_label.set_xticks([])
    ax_layer_label.set_yticks([])
    ax_minus = plt.axes([0.01, 0.25, 0.09, 0.04])
    btn_minus = Button(ax_minus, "-")
    ax_plus = plt.axes([0.12, 0.25, 0.09, 0.04])
    btn_plus = Button(ax_plus, "+")
    # Create Reset View button (always visible)
    ax_top = plt.axes([0.01, 0.20, 0.2, 0.04])
    btn_top = Button(ax_top, "Top View")
    # Create Reset View button (always visible)
    ax_side = plt.axes([0.01, 0.15, 0.2, 0.04])
    btn_side = Button(ax_side, "Side View")
    ax_layer_label.set_visible(True)
    ax_minus.set_visible(True)
    ax_plus.set_visible(True)
    ax_top.set_visible(True)
    ax_side.set_visible(True)

    def on_minus(event):
        if visualizer.selected_layer > 0:
            visualizer.selected_layer -= 1
            layer_text.set_text(f"Layer: {visualizer.selected_layer}")
            visualizer.schedule_draw()
            plt.draw()

    def on_plus(event):
        if visualizer.selected_layer < visualizer.total_layers - 1:
            visualizer.selected_layer += 1
            layer_text.set_text(f"Layer: {visualizer.selected_layer}")
            visualizer.schedule_draw()
            plt.draw()

    def on_top(event):
        visualizer.top_view()

    def on_side(event):
        visualizer.side_view()

    btn_minus.on_clicked(on_minus)
    btn_plus.on_clicked(on_plus)
    btn_top.on_clicked(on_top)
    btn_side.on_clicked(on_side)

    plt.show()


if __name__ == '__main__':
    main()
