import { useMemo } from 'react';
import {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  type Edge,
  type Node,
} from '@xyflow/react';

import { getBoxStatusPresentation } from '../boxStatus';
import type { LineageGraph } from '../types';

type Language = 'fr' | 'en';

type Props = {
  graph: LineageGraph;
  language: Language;
  onSelectBox: (boxId: number, globalCode: string) => void;
};

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
        fitViewOptions={{ padding: 0.22, maxZoom: 1 }}
        minZoom={0.12}
        maxZoom={1.8}
        nodesConnectable={false}
        nodesDraggable
        elementsSelectable
        panOnDrag
        zoomOnPinch
        zoomOnScroll
        onNodeClick={(_event, node) => {
          const boxId = Number(node.id);
          const globalCode = String(node.data.globalCode);
          if (boxId !== graph.root_box_id) {
            onSelectBox(boxId, globalCode);
          }
        }}
      >
        <Background gap={22} size={1} color="#dbe7ec" />
        <MiniMap
          pannable
          zoomable
          nodeColor={(node) => getStatusColor(String(node.data.status), Boolean(node.data.isRoot))}
          maskColor="rgba(248, 251, 252, 0.72)"
        />
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
  const levels = calculateLevels(graph);
  const nodesByLevel = new Map<number, LineageGraph['nodes']>();

  for (const graphNode of graph.nodes) {
    const level = levels.get(graphNode.id) ?? 0;
    nodesByLevel.set(level, [...(nodesByLevel.get(level) ?? []), graphNode]);
  }

  const nodes: Node[] = [];
  const sortedLevels = [...nodesByLevel.keys()].sort((left, right) => left - right);

  for (const level of sortedLevels) {
    const levelNodes = nodesByLevel.get(level) ?? [];
    const totalHeight = Math.max(0, (levelNodes.length - 1) * 118);

    levelNodes
      .sort((left, right) => left.global_code.localeCompare(right.global_code))
      .forEach((graphNode, index) => {
        const status = getBoxStatusPresentation(graphNode.status, language);

        nodes.push({
          id: String(graphNode.id),
          position: {
            x: level * 270,
            y: index * 118 - totalHeight / 2,
          },
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
            width: 190,
            border: graphNode.is_root ? '2px solid #106b87' : '1px solid #ccdbe2',
            borderRadius: 7,
            background: graphNode.is_root ? '#e2f3f7' : '#ffffff',
            color: '#151922',
            boxShadow: 'none',
            padding: 0,
          },
        });
      });
  }

  const edges: Edge[] = graph.edges.map((edge) => ({
    id: String(edge.id),
    source: String(edge.source),
    target: String(edge.target),
    type: 'smoothstep',
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: '#106b87',
      width: 18,
      height: 18,
    },
    style: {
      stroke: '#106b87',
      strokeWidth: 1.5,
    },
  }));

  return { nodes, edges };
}

function getStatusColor(status: string, isRoot: boolean) {
  const tone = getBoxStatusPresentation(status, 'fr').tone;

  if (tone === 'dead') return '#c4574e';
  if (tone === 'warning') return '#c18a2b';
  if (tone === 'neutral') return '#87939b';
  return isRoot ? '#106b87' : '#4f9471';
}

function calculateLevels(graph: LineageGraph) {
  const nodeIds = graph.nodes.map((node) => node.id);
  const indegrees = new Map(nodeIds.map((id) => [id, 0]));
  const children = new Map<number, number[]>();

  for (const edge of graph.edges) {
    indegrees.set(edge.target, (indegrees.get(edge.target) ?? 0) + 1);
    children.set(edge.source, [...(children.get(edge.source) ?? []), edge.target]);
  }

  const queue = nodeIds.filter((id) => (indegrees.get(id) ?? 0) === 0);
  const levels = new Map(queue.map((id) => [id, 0]));
  const processed = new Set<number>();

  while (queue.length) {
    const nodeId = queue.shift();
    if (nodeId === undefined) break;
    processed.add(nodeId);

    for (const childId of children.get(nodeId) ?? []) {
      levels.set(
        childId,
        Math.max(levels.get(childId) ?? 0, (levels.get(nodeId) ?? 0) + 1),
      );
      indegrees.set(childId, (indegrees.get(childId) ?? 1) - 1);
      if (indegrees.get(childId) === 0) {
        queue.push(childId);
      }
    }
  }

  for (const nodeId of nodeIds) {
    if (!processed.has(nodeId)) {
      levels.set(nodeId, levels.get(graph.root_box_id) ?? 0);
    }
  }

  return levels;
}
