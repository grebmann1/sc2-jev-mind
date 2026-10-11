// The host exports these hooks; SDK 0.1.0's bundled declarations omit them.
import '@zana-ai/zcc-plugin-sdk/app';
declare module '@zana-ai/zcc-plugin-sdk/app' {
  export function useRpc(): { call(method: string, args?: unknown): Promise<unknown> };
  export function useRealtime(event: string, handler: (payload: unknown) => void): void;
}
