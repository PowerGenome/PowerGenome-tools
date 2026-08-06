declare const process: {
    env: Record<string, string | undefined>;
};

// Minimal Buffer ambient declaration so specs can use Node's global Buffer
// without requiring @types/node.  Playwright's FilePayload.buffer is typed as
// Buffer, and Buffer is always available as a runtime global in Node.js.
interface Buffer extends Uint8Array {}
interface BufferConstructor {
    from(value: string, encoding?: string): Buffer;
    from(value: number[]): Buffer;
    from(value: ArrayBuffer | SharedArrayBuffer): Buffer;
}
declare const Buffer: BufferConstructor;
