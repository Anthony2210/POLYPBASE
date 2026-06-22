import { useMemo } from 'react';
import {
  Background,
  Controls,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
} from '@xyflow/react';

import { getBoxStatusPresentation } from '../boxStatus';
import type { LineageGraph, LineageGraphEdge, LineageGraphNode } from '../types';

type Language = 'fr' | 'en';

type Props = {
  graph: LineageGraph;
  language: Language;
  onSelectBox: (boxId: number, globalCode: string) => void;
};

type FamilyLayout = {
  ancestorIds: Set<number>;
  descendantIds: Set<number>;
  positions: Map<number, { x: number; y: number }>;
  visibleEdges: LineageGraphEdge[];
  visibleNodes: LineageGraphNode[];
};

const NODE_WIDTH = 194;
const COLUMN_GAP = 250;
const ROW_GAP = 112;

const labels = {
  fr: {
    current: 'Boîte actuelle',
    truncated: 'La famille est très grande. Seuls les 250 premiers éléments sont affichés.',
  },
  en: {
    current: 'Current box',
    truncated: 'This family is very large. Only the first 250 items are displayed.',
  },
};

export default function InteractiveLineageGraph({
  graph,
  language,
  onSelectBox,
}: Props) {
  const text = labels[language];
  const { nodes, edges } = useMemo(
    () => createFlowElements(graph, text.current, language),
    [graph, language, text.current],
  );

  return (
    <section className="interactive-lineage">
      {graph.truncated ? <p className="lineage-warning">{text.truncated}</p> : null}
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        fitViewOptions={{ padding: 0.26, maxZoom: 1.08 }}
        minZoom={0.18}
        maxZoom={1.8}
        nodesConnectable={false}
        nodesDraggable={false}
        elementsSelectable
        panOnDrag
        zoomOnPinch
        zoomOnScroll
        onlyRenderVisibleElements
        onNodeClick={(_event, node) => {
          const boxId = Number(node.id);
          const globalCode = String(node.data.globalCode);
          if (boxId !== graph.root_box_id) {
            onSelectBox(boxId, globalCode);
          }
        }}
      >
        <Background gap={24} size={1} color="#dbe7ec" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </section>
  );
}

function createFlowElements(
  graph: LineageGraph,
  currentLabel: string,
  language: Language,
) {
  const layout = createFocusedFamilyLayout(graph);

  const nodes: Node[] = layout.visibleNodes.map((graphNode) => {
    const status = getBoxStatusPresentation(graphNode.status, language);
    const position = layout.positions.get(graphNode.id) ?? { x: 0, y: 0 };

    return {
      id: String(graphNode.id),
      position,
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      data: {
        globalCode: graphNode.global_code,
        isRoot: graphNode.is_root,
        status: graphNode.status,
        label: (
          <div className="flow-box-node">
            <div className="flow-box-node-heading">
              {graphNode.is_root ? <span>{currentLabel}</span> : <span />}
              <span className={`box-life-status is-${status.tone}`}>
                {status.label}
              </span>
            </div>
            <strong>{graphNode.global_code}</strong>
            <small>{graphNode.species_name}</small>
            {graphNode.thermal_zone_name ? <small>{graphNode.thermal_zone_name}</small> : null}
          </div>
        ),
      },
      className: graphNode.is_root ? 'is-current' : '',
      style: {
        width: NODE_WIDTH,
        border: graphNode.is_root ? '2px solid #106b87' : '1px solid #ccdbe2',
        borderRadius: 7,
        background: graphNode.is_root ? '#e2f3f7' : '#ffffff',
        color: '#151922',
        boxShadow: 'none',
        padding: 0,
      },
    };
  });

  const edges: Edge[] = layout.visibleEdges.map((edge) => ({
    id: String(edge.id),
    source: String(edge.source),
    target: String(edge.target),
    type: 'smoothstep',
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: '#106b87',
      width: 16,
      height: 16,
    },
    style: {
      stroke: '#106b87',
      strokeWidth: 1.5,
    },
  }));

  return { nodes, edges };
}

function createFocusedFamilyLayout(graph: LineageGraph): FamilyLayout {
  const nodesById = new Map(graph.nodes.map((node) => [node.id, node]));
  const childIdsByParent = new Map<number, number[]>();
  const parentIdsByChild = new Map<number, number[]>();

  for (const edge of graph.edges) {
    childIdsByParent.set(edge.source, [...(childIdsByParent.get(edge.source) ?? []), edge.target]);
    parentIdsByChild.set(edge.target, [...(parentIdsByChild.get(edge.target) ?? []), edge.source]);
  }

  const descendantIds = collectRelatedIds(graph.root_box_id, childIdsByParent);
  const ancestorIds = collectRelatedIds(graph.root_box_id, parentIdsByChild);
  descendantIds.delete(graph.root_box_id);
  ancestorIds.delete(graph.root_box_id);

  const visibleIds = new Set([graph.root_box_id, ...ancestorIds, ...descendantIds]);
  const visibleNodes = graph.nodes.filter((node) => visibleIds.has(node.id));
  const visibleEdges = graph.edges.filter(
    (edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target),
  );
  const positions = new Map<number, { x: number; y: number }>();
  let nextLeafIndex = 0;

  function layoutDescendants(nodeId: number, depth: number, branch: Set<number>): number {
    const children = (childIdsByParent.get(nodeId) ?? [])
      .filter((id) => descendantIds.has(id) && !branch.has(id))
      .sort((left, right) => compareNodeCodes(nodesById.get(left), nodesById.get(right)));

    if (!children.length) {
      const leafY = nextLeafIndex * ROW_GAP;
      nextLeafIndex += 1;
      positions.set(nodeId, { x: depth * COLUMN_GAP, y: leafY });
      return leafY;
    }

    const childYs = children.map((childId) => {
      const nextBranch = new Set(branch);
      nextBranch.add(nodeId);
      return layoutDescendants(childId, depth + 1, nextBranch);
    });
    const nodeY = childYs.reduce((sum, value) => sum + value, 0) / childYs.length;
    positions.set(nodeId, { x: depth * COLUMN_GAP, y: nodeY });
    return nodeY;
  }

  const rootY = layoutDescendants(graph.root_box_id, 0, new Set());
  const ancestorLevels = getAncestorLevels(graph.root_box_id, parentIdsByChild, ancestorIds);

  for (const [level, ids] of ancestorLevels.entries()) {
    const sortedIds = [...ids].sort((left, right) => compareNodeCodes(nodesById.get(left), nodesById.get(right)));
    const offset = ((sortedIds.length - 1) * ROW_GAP) / 2;

    sortedIds.forEach((nodeId, index) => {
      positions.set(nodeId, {
        x: -level * COLUMN_GAP,
        y: rootY + index * ROW_GAP - offset,
      });
    });
  }

  return { ancestorIds, descendantIds, positions, visibleEdges, visibleNodes };
}

function collectRelatedIds(startId: number, adjacentIds: Map<number, number[]>) {
  const relatedIds = new Set<number>([startId]);
  const queue = [startId];

  while (queue.length) {
    const currentId = queue.shift();
    if (currentId == null) continue;

    for (const nextId of adjacentIds.get(currentId) ?? []) {
      if (relatedIds.has(nextId)) continue;
      relatedIds.add(nextId);
      queue.push(nextId);
    }
  }

  return relatedIds;
}

function getAncestorLevels(
  rootId: number,
  parentIdsByChild: Map<number, number[]>,
  ancestorIds: Set<number>,
) {
  const levels = new Map<number, number[]>();
  const visited = new Set<number>([rootId]);
  const queue: Array<{ id: number; level: number }> = [{ id: rootId, level: 0 }];

  while (queue.length) {
    const current = queue.shift();
    if (!current) continue;

    for (const parentId of parentIdsByChild.get(current.id) ?? []) {
      if (!ancestorIds.has(parentId) || visited.has(parentId)) continue;
      visited.add(parentId);
      const level = current.level + 1;
      levels.set(level, [...(levels.get(level) ?? []), parentId]);
      queue.push({ id: parentId, level });
    }
  }

  return levels;
}

function compareNodeCodes(left?: LineageGraphNode, right?: LineageGraphNode) {
  return (left?.global_code ?? '').localeCompare(right?.global_code ?? '');
}
