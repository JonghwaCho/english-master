/**
 * Auth bootstrap: wraps window.fetch to automatically:
 *   1. Attach Authorization: Bearer <access_token> header
 *   2. Redirect to /login on 401
 *   3. Auto-refresh access_token using the httpOnly refresh cookie
 *
 * Include this script FIRST in index.html <head> (before other scripts).
 */
(function() {
  const ACCESS_KEY = 'access_token';
  let refreshPromise = null;

  function getToken() {
    return localStorage.getItem(ACCESS_KEY) || '';
  }

  function setToken(token) {
    if (token) localStorage.setItem(ACCESS_KEY, token);
    else localStorage.removeItem(ACCESS_KEY);
  }

  async function refreshAccessToken() {
    if (refreshPromise) return refreshPromise;
    refreshPromise = (async () => {
      try {
        const res = await _originalFetch('/api/auth/refresh', {
          method: 'POST',
          credentials: 'include',
        });
        if (!res.ok) {
          setToken(null);
          return null;
        }
        const data = await res.json();
        setToken(data.access_token);
        return data.access_token;
      } catch {
        setToken(null);
        return null;
      } finally {
        refreshPromise = null;
      }
    })();
    return refreshPromise;
  }

  const _originalFetch = window.fetch.bind(window);

  window.fetch = async function patchedFetch(input, init = {}) {
    const url = typeof input === 'string' ? input : input.url;
    const isApi = url && (url.startsWith('/api/') || url.includes('://') && new URL(url).pathname.startsWith('/api/'));

    if (!isApi) return _originalFetch(input, init);

    const token = getToken();
    init = { ...init };
    init.headers = new Headers(init.headers || {});
    if (token) init.headers.set('Authorization', `Bearer ${token}`);
    init.credentials = init.credentials || 'include';

    let res = await _originalFetch(input, init);

    // On 401, try refresh and retry ONCE
    if (res.status === 401 && !url.includes('/api/auth/')) {
      const newToken = await refreshAccessToken();
      if (newToken) {
        init.headers.set('Authorization', `Bearer ${newToken}`);
        res = await _originalFetch(input, init);
      } else {
        // Refresh failed → redirect to login
        window.location.href = '/login';
        throw new Error('Authentication required');
      }
    }

    return res;
  };

  // On page load, verify we have a valid token; if not and not on /login, redirect
  async function verifyAuth() {
    const publicPages = ['/login', '/signup'];
    if (publicPages.includes(window.location.pathname)) return;

    // Handle OAuth redirect (access_token in URL fragment)
    if (window.location.hash.startsWith('#access_token=')) {
      const token = window.location.hash.replace('#access_token=', '');
      setToken(token);
      history.replaceState(null, '', window.location.pathname);
    }

    const token = getToken();
    if (!token) {
      // Try to refresh from cookie
      const refreshed = await refreshAccessToken();
      if (!refreshed) {
        window.location.href = '/login';
        return;
      }
    }

    try {
      const res = await fetch('/api/auth/me');
      if (res.ok) {
        const user = await res.json();
        window.__currentUser = user;
        // Fire an event for any other code that wants to react
        window.dispatchEvent(new CustomEvent('auth:ready', { detail: user }));
      }
    } catch {
      window.location.href = '/login';
    }
  }

  // Logout helper (global)
  window.authLogout = async function() {
    try {
      await fetch('/api/auth/logout', { method: 'POST' });
    } catch {}
    setToken(null);
    window.location.href = '/login';
  };

  window.authCurrentUser = function() {
    return window.__currentUser || null;
  };

  // Run verification on DOMContentLoaded
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', verifyAuth);
  } else {
    verifyAuth();
  }
})();
