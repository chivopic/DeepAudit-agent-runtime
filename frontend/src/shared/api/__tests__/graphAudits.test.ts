import { describe, it, expect, vi, beforeEach } from "vitest";
import type { Mock } from "vitest";

vi.mock("../serverClient", () => ({
	apiClient: {
		get: vi.fn(),
		post: vi.fn(),
		put: vi.fn(),
		delete: vi.fn(),
		patch: vi.fn(),
	},
}));

import { apiClient } from "../serverClient";
import {
	startGraphAudit,
	getGraphAudit,
	cancelGraphAudit,
	resumeGraphAudit,
	getGraphAuditFindings,
	getGraphAuditEvents,
	graphAuditEventStreamUrl,
} from "../graphAudits";

describe("graphAudits API", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	describe("startGraphAudit", () => {
		it("POSTs to /graph-audits/ and returns the accepted task", async () => {
			const task = { id: "aud_1", status: "running" };
			(apiClient.post as Mock).mockResolvedValue({ data: task });

			const result = await startGraphAudit({
				project_id: "p1",
				source_type: "project",
			});

			expect(apiClient.post).toHaveBeenCalledWith("/graph-audits/", {
				project_id: "p1",
				source_type: "project",
			});
			expect(result).toEqual(task);
		});

		it("passes a branch through for project sources", async () => {
			(apiClient.post as Mock).mockResolvedValue({ data: { id: "a", status: "running" } });

			await startGraphAudit({
				project_id: "p1",
				source_type: "project",
				branch_name: "develop",
			});

			expect(apiClient.post).toHaveBeenCalledWith(
				"/graph-audits/",
				expect.objectContaining({ branch_name: "develop" }),
			);
		});

		it("supports inline fixture files for offline runs", async () => {
			(apiClient.post as Mock).mockResolvedValue({ data: { id: "a", status: "completed" } });

			await startGraphAudit({ fixture_files: { "app.py": "import os\n" } });

			expect(apiClient.post).toHaveBeenCalledWith(
				"/graph-audits/",
				expect.objectContaining({ fixture_files: { "app.py": "import os\n" } }),
			);
		});
	});

	describe("getGraphAudit", () => {
		it("GETs the task by id", async () => {
			const task = { id: "aud_1", status: "completed" };
			(apiClient.get as Mock).mockResolvedValue({ data: task });

			expect(await getGraphAudit("aud_1")).toEqual(task);
			expect(apiClient.get).toHaveBeenCalledWith("/graph-audits/aud_1");
		});
	});

	describe("cancelGraphAudit", () => {
		it("POSTs to the cancel endpoint", async () => {
			(apiClient.post as Mock).mockResolvedValue({ data: { id: "aud_1", status: "cancelled" } });

			const result = await cancelGraphAudit("aud_1");

			expect(apiClient.post).toHaveBeenCalledWith("/graph-audits/aud_1/cancel");
			expect(result.status).toBe("cancelled");
		});
	});

	describe("resumeGraphAudit", () => {
		it("POSTs to the resume endpoint", async () => {
			(apiClient.post as Mock).mockResolvedValue({ data: { id: "aud_1", status: "running" } });

			await resumeGraphAudit("aud_1");

			expect(apiClient.post).toHaveBeenCalledWith("/graph-audits/aud_1/resume");
		});
	});

	describe("getGraphAuditFindings", () => {
		it("GETs findings for the task", async () => {
			const findings = [{ id: "fnd_1", title: "SQL Injection" }];
			(apiClient.get as Mock).mockResolvedValue({ data: findings });

			expect(await getGraphAuditFindings("aud_1")).toEqual(findings);
			expect(apiClient.get).toHaveBeenCalledWith("/graph-audits/aud_1/findings");
		});
	});

	describe("graphAuditEventStreamUrl", () => {
		it("points at the SSE endpoint under the v1 prefix", () => {
			expect(graphAuditEventStreamUrl("aud_1")).toBe(
				"/api/v1/graph-audits/aud_1/events/stream",
			);
		});
	});

	describe("getGraphAuditEvents", () => {
		it("GETs events for the task", async () => {
			const events = [{ kind: "task_start", message: "langgraph audit started" }];
			(apiClient.get as Mock).mockResolvedValue({ data: events });

			expect(await getGraphAuditEvents("aud_1")).toEqual(events);
			expect(apiClient.get).toHaveBeenCalledWith("/graph-audits/aud_1/events");
		});
	});
});
