declare module 'virtual:pipeline-info' {
  export interface PipelineFile {
    stage: string;
    file: string;
    producer: string;
    producerExists: boolean;
    exists: boolean;
    bytes: number;
    modified: string | null;
    rows: number;
    columns: number;
  }
  const info: {
    generatedAt: string;
    files: PipelineFile[];
    model: { source: string; nEstimators: number | null; contamination: number | null; randomState: number | null };
  };
  export default info;
}
