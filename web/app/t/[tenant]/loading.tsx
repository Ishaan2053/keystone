import { GraphLoader } from '@/components/graph-loader';
import { dataSource } from '@/lib/data-source';
export default function Loading() {
  const live = dataSource() === 'api';
  return (
    <main className="state-page loading-page">
      <GraphLoader
        label={live ? 'Loading live workspace…' : 'Loading fixture workspace…'}
        detail={
          live
            ? 'Reading the org’s history from the FalkorDB graph'
            : 'Reading the curated fixtures'
        }
      />
    </main>
  );
}
