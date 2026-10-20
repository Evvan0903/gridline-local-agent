import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, it, expect, vi } from "vitest";
import { Benchmark } from "./Benchmark";
import { api, post } from "../api";
vi.mock("../api", () => ({ api: vi.fn(), post: vi.fn() }));
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api).mockImplementation(async (path) =>
    path.startsWith("/benchmarks/catalog")
      ? {
          models: ["qwen3:8b", "gpt-6-luna", "gpt-6.1-sol"],
          sources: [{ id: "f", name: "Synthetic.xlsx" }],
          tests: ["A schema"],
          rules_name: "Original rules",
          sol_ready: false,
        }
      : [],
  );
});
it("mounts history and catalog with top-level hooks and never submits automatically", async () => {
  const error = vi.fn();
  render(<Benchmark workspaceId="w" onError={error} />);
  expect(
    await screen.findByRole("checkbox", { name: "gpt-6-luna" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", {
      name: "Start local comparison / record cloud NOT RUN",
    }),
  ).toBeDisabled();
  expect(post).not.toHaveBeenCalled();
  expect(error).not.toHaveBeenCalled();
});
it("sends unchecked cloud authorizations as false, not implicit approval", async () => {
  vi.mocked(post).mockResolvedValue({
    id: "b",
    status: "completed",
    results: [
      { model: "gpt-6-luna", status: "NOT RUN", api_requests: 0, api_usd: "0" },
    ],
  });
  render(<Benchmark workspaceId="w" onError={vi.fn()} />);
  await screen.findByRole("checkbox", { name: "gpt-6-luna" });
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Benchmark dataset" }),
    "f",
  );
  await userEvent.click(screen.getByRole("checkbox", { name: "qwen3:8b" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "gpt-6-luna" }));
  await userEvent.click(
    screen.getByRole("button", {
      name: "Start local comparison / record cloud NOT RUN",
    }),
  );
  expect(post).toHaveBeenCalledWith(
    "/benchmarks",
    expect.objectContaining({
      models: ["gpt-6-luna"],
      paid_authorized: false,
      data_authorized: false,
      run_cap: "0.25",
    }),
  );
  await waitFor(() => expect(screen.getByText("NOT RUN")).toBeInTheDocument());
});
