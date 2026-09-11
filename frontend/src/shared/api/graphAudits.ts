/**
 * Graph Audits API
 * LangGraph 审计路径（/api/v1/graph-audits/*）。
 *
 * 与 agentTasks 并行的实验性引擎，返回的任务对象刻意与 AgentTask 同形，
 * 以便现有展示组件可以直接复用。
 */

import { apiClient } from "./serverClient";
import type { AgentFinding, AgentTask } from "./agentTasks";

// ============ Types ============

/** 审计源。`project` 由服务端根据项目记录解析路径，客户端不提供路径。 */
export type GraphAuditSourceType = "project" | "local";

export interface StartGraphAuditRequest {
  project_id?: string;
  name?: string;
  source_type?: GraphAuditSourceType;
  /** 仅 source_type="project"：留空则用项目自身的默认分支。 */
  branch_name?: string;
  languages?: string[];
  include_paths?: string[];
  exclude_paths?: string[];
  /** 内联代码片段，用于离线/演示运行。 */
  fixture_files?: Record<string, string>;
  max_tokens?: number;
  max_model_calls?: number;
  /** 同步等待整轮跑完（仅测试场景，服务端可能拒绝）。 */
  wait?: boolean;
}

export interface GraphAuditEvent {
  kind?: string;
  message?: string;
  [key: string]: unknown;
}

/** 启动接口默认返回 202，任务在后台继续跑。 */
export interface StartGraphAuditResponse extends Partial<AgentTask> {
  id: string;
  status: string;
}

// ============ API ============

/** 启动一次 LangGraph 审计。 */
export async function startGraphAudit(
  data: StartGraphAuditRequest,
): Promise<StartGraphAuditResponse> {
  const response = await apiClient.post("/graph-audits/", data);
  return response.data;
}

/** 读取任务状态。 */
export async function getGraphAudit(taskId: string): Promise<AgentTask> {
  const response = await apiClient.get(`/graph-audits/${taskId}`);
  return response.data;
}

/** 取消任务（协作式取消，节点在下一个检查点停下）。 */
export async function cancelGraphAudit(
  taskId: string,
): Promise<{ status: string; id: string }> {
  const response = await apiClient.post(`/graph-audits/${taskId}/cancel`);
  return response.data;
}

/** 从检查点恢复任务。 */
export async function resumeGraphAudit(taskId: string): Promise<AgentTask> {
  const response = await apiClient.post(`/graph-audits/${taskId}/resume`);
  return response.data;
}

/** 读取审计结果。 */
export async function getGraphAuditFindings(
  taskId: string,
): Promise<AgentFinding[]> {
  const response = await apiClient.get(`/graph-audits/${taskId}/findings`);
  return response.data;
}

/** 读取事件列表。 */
export async function getGraphAuditEvents(
  taskId: string,
): Promise<GraphAuditEvent[]> {
  const response = await apiClient.get(`/graph-audits/${taskId}/events`);
  return response.data;
}

/**
 * 事件流地址（SSE）。
 *
 * 帧的字段形状与 /agent-tasks/{id}/events 一致（服务端已把 graph 的 `kind`
 * 映射到 AgentEvent 的 `type` 词汇），所以现有的流处理逻辑可以直接复用。
 */
export function graphAuditEventStreamUrl(taskId: string): string {
  return `/api/v1/graph-audits/${taskId}/events/stream`;
}
