import fs from 'node:fs';
import path from 'node:path';
import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';

// The pipeline CSVs live in ../data and ../dataset (outside the Vite root). They are imported
// directly with `?raw` so there is a single copy of the data; fs.allow lets the dev server read them.

const PROJECT_ROOT = path.resolve(__dirname, '..');

// Files the Settings page reports on. Sizes, modified times and CSV shapes are read from disk when
// the dev server / build runs, because a browser page cannot see the filesystem. Nothing here is
// written by the frontend and nothing is estimated: if a file is missing it is reported as missing.
const PIPELINE_FILES = [
  { stage: 'Raw data', file: 'dataset/synthetic_bitcoin_transactions.csv', producer: 'generate_dataset.py' },
  { stage: 'Feature engineering', file: 'data/wallet_behavior_features.csv', producer: 'ml/feature_engineering.py' },
  { stage: 'Isolation Forest', file: 'data/anomaly_results.csv', producer: 'ml/anomaly_detection.py' },
  { stage: 'Forensic rules', file: 'data/forensic_results.csv', producer: 'ml/forensic_rules.py' },
  { stage: 'Result fusion', file: 'data/fusion_results.csv', producer: 'ml/result_fusion.py' },
];
const VIRTUAL_ID = 'virtual:pipeline-info';

function describeFile(rel: string) {
  const abs = path.join(PROJECT_ROOT, rel);
  try {
    const st = fs.statSync(abs);
    const text = fs.readFileSync(abs, 'utf8');
    const lines = text.split(/\r?\n/).filter((l) => l.length > 0);
    return {
      exists: true,
      bytes: st.size,
      modified: st.mtime.toISOString(),
      rows: Math.max(0, lines.length - 1),
      columns: lines[0] ? lines[0].replace(/^﻿/, '').split(',').length : 0,
    };
  } catch {
    return { exists: false, bytes: 0, modified: null, rows: 0, columns: 0 };
  }
}

function readModelConfig() {
  const rel = 'ml/anomaly_detection.py';
  try {
    const src = fs.readFileSync(path.join(PROJECT_ROOT, rel), 'utf8');
    const num = (name: string) => {
      const m = src.match(new RegExp(`^${name}\\s*=\\s*([0-9.]+)`, 'm'));
      return m ? Number(m[1]) : null;
    };
    return {
      source: rel,
      nEstimators: num('N_ESTIMATORS'),
      contamination: num('CONTAMINATION'),
      randomState: num('RANDOM_STATE'),
    };
  } catch {
    return { source: rel, nEstimators: null, contamination: null, randomState: null };
  }
}

function pipelineInfo(): Plugin {
  return {
    name: 'sih146-pipeline-info',
    resolveId: (id) => (id === VIRTUAL_ID ? '\0' + VIRTUAL_ID : null),
    load(id) {
      if (id !== '\0' + VIRTUAL_ID) return null;
      // Re-read this module whenever a pipeline file changes on disk (e.g. after re-running ml/*.py).
      for (const f of PIPELINE_FILES) this.addWatchFile(path.join(PROJECT_ROOT, f.file));
      const info = {
        generatedAt: new Date().toISOString(),
        files: PIPELINE_FILES.map((f) => ({ ...f, ...describeFile(f.file), producerExists: fs.existsSync(path.join(PROJECT_ROOT, f.producer)) })),
        model: readModelConfig(),
      };
      return `export default ${JSON.stringify(info)};`;
    },
  };
}

export default defineConfig({
  plugins: [react(), pipelineInfo()],
  server: { fs: { allow: ['..'] } },
});
