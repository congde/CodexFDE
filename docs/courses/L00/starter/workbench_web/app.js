"use strict";

async function showShell() {
  const status = document.getElementById("shell-status");
  const database = document.getElementById("database-path");
  const button = document.getElementById("check-shell");
  button.disabled = true;
  status.textContent = "正在核对页面壳……";
  database.textContent = "等待本次检查";
  try {
    const response = await fetch("/api/health", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const health = await response.json();
    if (health.surface !== "workbench" || !Array.isArray(health.capabilities) || !health.capabilities.includes("shell")) {
      throw new Error("返回的服务身份不符合当前工作台约定");
    }
    database.textContent = health.database;
    status.textContent = health.database_exists
      ? "页面壳已响应；计划位置存在数据库文件，请核对其来源。本壳不读写数据库。"
      : "页面壳已响应；尚未创建数据库，账本与执行功能待建设。";
  } catch (error) {
    database.textContent = "未确认（本次检查失败）";
    status.textContent = `核对失败：${error.message}。请查看启动终端，保留原错误后再检查。`;
  } finally {
    button.disabled = false;
  }
}

document.getElementById("check-shell").addEventListener("click", showShell);
showShell();
