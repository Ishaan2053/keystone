'use client';
import Image from 'next/image';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import keystoneLogo from '@/assets/keystone-logo.png';
import type { Stats, TenantSummary } from '@/lib/types';
import type { WorkspaceSource } from '@/lib/workspace-data';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { CircleHelp, ExternalLink, Webhook } from 'lucide-react';
import { SANDBOX_ISSUES_URL } from '@/lib/sandbox';
import { ThemeToggle } from './theme-toggle';
import { Button } from './ui/button';
import { WelcomeDialog, openWelcome } from './welcome-dialog';
export function Header({
  tenant,
  tenants,
  source = 'fixture',
  stats,
  statsLoading = false,
  onOpenProof,
}: {
  tenant: string;
  tenants: TenantSummary[];
  source?: WorkspaceSource;
  stats?: Stats | null;
  statsLoading?: boolean;
  onOpenProof?: () => void;
}) {
  const router = useRouter();
  const measured = source === 'live' && stats?.proof && stats.proof.n > 0;
  return (
    <header className="app-header">
      <Link className="wordmark" href="/">
        <Image src={keystoneLogo} alt="" width={40} height={26} className="wordmark-mark" preload />
        Keystone
      </Link>
      <span className="header-divider" />
      <Select
        value={tenant}
        onValueChange={(value) => router.push(`/t/${encodeURIComponent(value)}`)}
      >
        <SelectTrigger aria-label="Tenant">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {tenants.map((t) => (
            <SelectItem value={t.slug} key={t.slug}>
              {t.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <span className={`badge type-caption source-badge source-${source}`} data-source={source}>
        <span className="source-dot" aria-hidden="true" />
        {source === 'live' ? 'Live data' : 'Fixture data'}
      </span>
      <div className="header-end">
        {source === 'live' && (
          <a
            className="sandbox-header-link type-caption"
            href={SANDBOX_ISSUES_URL}
            target="_blank"
            rel="noreferrer"
            title="Open an issue in the sandbox repo; Keystone triages it live"
            data-sandbox-link
          >
            <Webhook aria-hidden="true" />
            <span>Sandbox issues</span>
            <ExternalLink aria-hidden="true" className="sandbox-external" />
          </a>
        )}
        <button
          type="button"
          className="proof proof-trigger type-caption"
          onClick={onOpenProof}
          aria-haspopup="dialog"
          data-tour="proof"
        >
          {measured ? (
            <>
              <span className="type-metric">
                {Number((stats.proof!.methods.graph.reviewer_hit_at_3 * 100).toFixed(1))}%
              </span>
              <span>Proof · reviewer hit@3</span>
            </>
          ) : source === 'live' && !stats ? (
            statsLoading ? (
              'Proof loading…'
            ) : (
              'Proof unavailable'
            )
          ) : source === 'fixture' && stats?.proof ? (
            'Fixture Proof'
          ) : stats?.unavailable.includes('proof') ? (
            'Proof unavailable'
          ) : (
            'Proof not measured'
          )}
        </button>
        <Button
          variant="ghost"
          className="help-button"
          onClick={openWelcome}
          aria-label="How Keystone works"
          data-tour="help"
        >
          <CircleHelp />
          <span className="help-label">Help</span>
        </Button>
        <ThemeToggle />
      </div>
      <WelcomeDialog />
    </header>
  );
}
