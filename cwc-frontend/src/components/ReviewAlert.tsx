import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { reviewApi } from '../api/client';

const POLL_MS = 15_000;

/**
 * The one thing on the Inbox that is allowed to shout.
 *
 * A document held for review is work waiting on a person, and unlike everything
 * else on this page it does not resolve itself. It sits in Manual-Review until
 * someone opens it — so it gets a red banner at the top of the page rather than
 * a number in a strip that reads 0 for most of the day and is therefore stopped
 * being looked at.
 *
 * Renders nothing at zero. A permanent "0 pending" bar trains people to ignore
 * the space it occupies, which is exactly the space that needs to be noticed on
 * the day it isn't zero.
 */
export default function ReviewAlert({ refreshKey }: { refreshKey?: number }) {
  const [count, setCount] = useState(0);

  const load = useCallback(() => {
    reviewApi.queue()
      .then(page => setCount(page.totalElements ?? page.content?.length ?? 0))
      .catch(() => { /* the Inbox already surfaces a failed load; stay quiet */ });
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [load, refreshKey]);

  if (count < 1) return null;

  return (
    <Link to="/review" className="review-alert lift" role="alert">
      <span className="review-alert-icon" aria-hidden="true">
        <svg width="17" height="17" viewBox="0 0 16 16" fill="none" stroke="currentColor"
             strokeWidth="1.5">
          <path d="M8 1.8 1.5 13.2h13z" strokeLinejoin="round" />
          <path d="M8 6.2v3.1" strokeLinecap="round" />
          <circle cx="8" cy="11.2" r=".7" fill="currentColor" stroke="none" />
        </svg>
      </span>
      <span className="review-alert-text">
        You have <b>{count}</b> document{count !== 1 ? 's' : ''} pending review
      </span>
      <span className="review-alert-cta">Open review queue →</span>
    </Link>
  );
}
