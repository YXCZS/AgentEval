import type { Metadata } from "next";
import "./globals.css";
import { AuthProvider } from "./auth-context";

export const metadata: Metadata = {
  title: "Agent Eval 评测工作台",
  description: "面向 RAG、Tool 和 Custom Agent 的 Trace 评测工作台",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body><AuthProvider>{children}</AuthProvider></body></html>;
}
