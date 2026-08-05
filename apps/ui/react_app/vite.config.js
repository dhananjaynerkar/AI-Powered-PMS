import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig(({ mode }) => {
    const env = loadEnv(mode, ".", "PMS_");
    const apiProxyTarget = env.PMS_API_PROXY_TARGET || "http://127.0.0.1:8001";
    return {
        plugins: [react()],
        server: {
            host: "127.0.0.1",
            port: 5173,
            proxy: {
                "/api": apiProxyTarget,
                "/auth": apiProxyTarget,
                "/health": apiProxyTarget
            }
        }
    };
});
