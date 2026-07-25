/** Host-owned save operation for exact artifact bytes. */
export interface ArtifactDownloadPort {
  saveArtifact(input: {
    readonly filename: string;
    readonly contentType: string;
    readonly body: ReadableStream<Uint8Array>;
  }): Promise<void>;
}
