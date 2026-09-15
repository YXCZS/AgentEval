export const API_URL = process.env.NEXT_PUBLIC_API_URL?.trim() ?? "";
export const PROJECT_ID = process.env.NEXT_PUBLIC_PROJECT_ID?.trim() ?? "";
export const SESSION = process.env.NEXT_PUBLIC_WORKSPACE_SESSION?.trim() ?? "";

export async function fetchApi(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  if (typeof input === "string" && !API_URL) {
    throw new Error("未配置 NEXT_PUBLIC_API_URL，无法连接 Agent Eval API。请在 Web 环境变量中配置 API 地址。");
  }
  if (typeof input === "string" && input.includes("/projects//")) {
    throw new Error("未配置 NEXT_PUBLIC_PROJECT_ID，无法确定当前项目。");
  }
  try {
    return await fetch(input, init);
  } catch {
    throw new Error("无法连接 API，请确认后端服务已启动，并检查 API 地址配置。");
  }
}
