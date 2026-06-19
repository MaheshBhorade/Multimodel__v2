// Global Application State
const state = {
  activeTab: 'overview',
  devices: [],
  captures: [],
  library: [],
  unknowns: [],
  pollingInterval: null,
  isPolling: true,
  capturesPage: 0,
  capturesLimit: 100,
  totalCaptures: 0,
  eventSource: null,
  platforms: []
};

// Chart.js references
let shareChart = null;
let adChart = null;

// DOM Elements
const elements = {
  navItems: document.querySelectorAll('.nav-item'),
  tabPanes: document.querySelectorAll('.tab-pane'),
  tabTitle: document.getElementById('current-tab-title'),
  tabDesc: document.getElementById('current-tab-desc'),
  
  // Controls
  autoRefresh: document.getElementById('auto-refresh'),
  manualRefresh: document.getElementById('manual-refresh'),
  toastContainer: document.getElementById('toast-container'),
  reviewBadge: document.getElementById('review-badge'),
  
  // KPI Metrics
  metricDevices: document.getElementById('metric-devices'),
  metricCaptures: document.getElementById('metric-captures'),
  metricReviews: document.getElementById('metric-reviews'),
  metricRatio: document.getElementById('metric-ratio'),
  
  // Lists / Tables
  capturesList: document.getElementById('captures-list'),
  reviewList: document.getElementById('review-list'),
  libraryList: document.getElementById('library-list'),
  platformsList: document.getElementById('platforms-list'),
  devicesList: document.getElementById('devices-list'),
  
  // Resolve Modal
  resolveModal: document.getElementById('resolve-modal'),
  closeResolveModal: document.getElementById('close-resolve-modal'),
  resolveForm: document.getElementById('resolve-form'),
  resolveCaptureId: document.getElementById('resolve-capture-id'),
  resolveImg: document.getElementById('resolve-img'),
  resolveOcr: document.getElementById('resolve-ocr'),
  resolveAudio: document.getElementById('resolve-audio'),
  
  // Add Library Modal
  addLibraryModal: document.getElementById('add-library-modal'),
  btnOpenAddLib: document.getElementById('btn-add-library'),
  closeAddModal: document.getElementById('close-add-modal'),
  addLibraryForm: document.getElementById('add-library-form')
};

// Tab Descriptions
const tabMeta = {
  overview: { title: 'Dashboard Overview', desc: 'Monitor stream status and system metrics in real-time.' },
  review: { title: 'Review Queue', desc: 'Analyze and resolve unrecognized content feeds captured by edge agents.' },
  library: { title: 'Reference Content Library', desc: 'Manage fingerprint signatures and category tags for match optimization.' },
  platforms: { title: 'Logo & Channel reference database', desc: 'Manage TV channel and platform references used for layout matching.' },
  devices: { title: 'Connected Edge Devices', desc: 'Inspect status, locations, and throughput profiles of Raspberry Pi hardware.' },
  analytics: { title: 'Analytics Reports', desc: 'Inspect aggregated system statistics, content categories, ad frequencies, and playback timelines.' }
};

// Initialize Application
document.addEventListener('DOMContentLoaded', () => {
  setupNavigation();
  setupEventListeners();
  startSSE();
});

// Helper to navigate to a tab and update UI/State
function navigateToTab(tabId, updateHash = false) {
  if (!tabMeta[tabId]) return;
  
  state.activeTab = tabId;
  
  // Update sidebar active classes
  elements.navItems.forEach(nav => {
    if (nav.getAttribute('data-tab') === tabId) {
      nav.classList.add('active');
    } else {
      nav.classList.remove('active');
    }
  });
  
  // Update panel view
  elements.tabPanes.forEach(pane => {
    if (pane.id === `tab-${tabId}`) {
      pane.classList.add('active');
    } else {
      pane.classList.remove('active');
    }
  });
  
  // Update Header Text
  elements.tabTitle.textContent = tabMeta[tabId].title;
  elements.tabDesc.textContent = tabMeta[tabId].desc;
  
  if (updateHash) {
    window.location.hash = tabId;
  }
  
  // Load specific view data immediately
  fetchData();
}

// Setup Sidebar Tab Navigation
function setupNavigation() {
  // Listen for hash changes to support browser history (back/forward buttons)
  window.addEventListener('hashchange', () => {
    const tabId = window.location.hash.replace('#', '') || 'overview';
    if (state.activeTab !== tabId) {
      navigateToTab(tabId, false);
    }
  });

  // Handle click on nav items
  elements.navItems.forEach(item => {
    item.addEventListener('click', () => {
      const tabId = item.getAttribute('data-tab');
      navigateToTab(tabId, true);
    });
  });

  // Initialize from current hash on load
  const initialTab = window.location.hash.replace('#', '') || 'overview';
  navigateToTab(initialTab, true);
}

// Setup Event Listeners for Modals and Controls
function setupEventListeners() {
  // Manual refresh button
  elements.manualRefresh.addEventListener('click', () => {
    showToast('Info', 'Fetching latest data...', 'info');
    fetchData();
  });

  // Auto-refresh toggler
  elements.autoRefresh.addEventListener('change', (e) => {
    state.isPolling = e.target.checked;
    if (state.isPolling) {
      startSSE();
      showToast('Live Sync Active', 'SSE Stream connected', 'info');
    } else {
      stopSSE();
      showToast('Live Sync Paused', 'Manual refresh required', 'info');
    }
  });

  // Resolve Modal closing
  elements.closeResolveModal.addEventListener('click', hideResolveModal);
  elements.resolveModal.addEventListener('click', (e) => {
    if (e.target === elements.resolveModal) hideResolveModal();
  });

  // Resolve Modal Submit
  elements.resolveForm.addEventListener('submit', handleResolveSubmit);

  // Add Library Modal Open/Close
  elements.btnOpenAddLib.addEventListener('click', showAddLibraryModal);
  elements.closeAddModal.addEventListener('click', hideAddLibraryModal);
  elements.addLibraryModal.addEventListener('click', (e) => {
    if (e.target === elements.addLibraryModal) hideAddLibraryModal();
  });

  // Add Library Modal Submit
  elements.addLibraryForm.addEventListener('submit', handleAddLibrarySubmit);

  // Pagination controls
  const prevBtn = document.getElementById('btn-prev-page');
  const nextBtn = document.getElementById('btn-next-page');
  if (prevBtn) {
    prevBtn.addEventListener('click', () => {
      if (state.capturesPage > 0) {
        state.capturesPage--;
        fetchData();
      }
    });
  }
  if (nextBtn) {
    nextBtn.addEventListener('click', () => {
      const totalPages = Math.ceil(state.totalCaptures / state.capturesLimit) || 1;
      if (state.capturesPage + 1 < totalPages) {
        state.capturesPage++;
        fetchData();
      }
    });
  }

  // Platforms tab - Load library content button
  const btnLoadLibraryPlat = document.getElementById('btn-load-library-plat');
  if (btnLoadLibraryPlat) {
    btnLoadLibraryPlat.addEventListener('click', loadAndRenderLibraryPlat);
  }
}

// Server-Sent Events (SSE) and Fallback Polling
function startSSE() {
  stopSSE();
  if (!state.isPolling) return;
  
  // 30s fallback poll to keep UI fresh if stream drops
  state.pollingInterval = setInterval(fetchData, 30000);
  
  try {
    state.eventSource = new EventSource('/api/v1/events');
    state.eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'update') {
          fetchData();
        }
      } catch (e) {
        console.error('Failed to parse SSE event:', e);
      }
    };
    state.eventSource.onerror = () => {
      console.warn('SSE disconnected, browser will automatically retry.');
    };
  } catch (e) {
    console.error('EventSource initialization failed:', e);
  }
}

function stopSSE() {
  if (state.pollingInterval) {
    clearInterval(state.pollingInterval);
    state.pollingInterval = null;
  }
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
}

// Global API Data Fetcher
async function fetchData() {
  try {
    const [devices, captures, library, unknowns, timeline, platforms] = await Promise.all([
      fetch('/api/v1/devices').then(r => r.json()),
      fetch(`/api/v1/captures?limit=${state.capturesLimit}&offset=${state.capturesPage * state.capturesLimit}`).then(r => r.json()),
      fetch('/api/v1/library').then(r => r.json()),
      fetch('/api/v1/captures?only_unknown=true&limit=50').then(r => r.json()),
      fetch('/api/v1/analytics/timeline').then(r => r.json()),
      fetch('/api/v1/platforms').then(r => r.json()).catch(() => [])
    ]);

    state.devices = devices;
    state.captures = captures.items;
    state.totalCaptures = captures.total;
    state.library = library;
    state.unknowns = unknowns.items;
    state.timeline = timeline;
    state.platforms = platforms;

    updateKPIs();
    renderActiveTab();
  } catch (error) {
    console.error('Error fetching dashboard data:', error);
    showToast('Sync Error', 'Failed to connect to backend server.', 'danger');
  }
}

// Update KPI Metric Cards
function updateKPIs() {
  const activeCount = state.devices.filter(d => d.status === 'active').length;
  elements.metricDevices.textContent = activeCount;
  elements.metricCaptures.textContent = state.captures.length > 0 ? state.captures[0].id : 0;
  
  const unknownCount = state.unknowns.length;
  elements.metricReviews.textContent = unknownCount;
  elements.reviewBadge.textContent = unknownCount;
  elements.reviewBadge.style.display = unknownCount > 0 ? 'inline-block' : 'none';

  if (state.captures.length > 0) {
    const ratio = (state.captures.filter(c => c.result?.content_type === 'unknown').length / state.captures.length) * 100;
    elements.metricRatio.textContent = `${ratio.toFixed(1)}%`;
  } else {
    elements.metricRatio.textContent = '0.0%';
  }
}

// Render active tab contents
function renderActiveTab() {
  switch (state.activeTab) {
    case 'overview':
      renderOverview();
      break;
    case 'review':
      renderReviewQueue();
      break;
    case 'library':
      renderLibrary();
      break;
    case 'platforms':
      renderPlatforms();
      break;
    case 'devices':
      renderDevices();
      break;
    case 'analytics':
      renderAnalytics();
      break;
  }
}

// Render: OVERVIEW (Real-Time Capture Log)
function formatTime(secs) {
  if (secs === null || secs === undefined) return '-';
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = Math.floor(secs % 60);
  return [
      h > 0 ? h : null,
      m.toString().padStart(2, '0'),
      s.toString().padStart(2, '0')
  ].filter(x => x !== null).join(':');
}

function renderOverview() {
  if (state.captures.length === 0) {
    elements.capturesList.innerHTML = '<tr><td colspan="9" class="loading-state">No captures recorded yet. Run crp-edge agent.</td></tr>';
    return;
  }

  // Display paginated captures directly
  const items = state.captures;
  
  // Update Pagination Controls UI
  const prevBtn = document.getElementById('btn-prev-page');
  const nextBtn = document.getElementById('btn-next-page');
  const pageIndicator = document.getElementById('page-indicator');
  if (prevBtn && nextBtn && pageIndicator) {
    prevBtn.disabled = state.capturesPage === 0;
    const totalPages = Math.ceil(state.totalCaptures / state.capturesLimit) || 1;
    nextBtn.disabled = (state.capturesPage + 1) >= totalPages;
    pageIndicator.textContent = `Page ${state.capturesPage + 1} of ${totalPages}`;
  }
  elements.capturesList.innerHTML = items.map(c => {
    const date = new Date(c.captured_at).toLocaleTimeString();
    
    // Confidence indicator
    let confClass = 'low';
    const confidence = c.result ? c.result.confidence : 0;
    if (confidence >= 0.8) confClass = 'high';
    else if (confidence >= 0.55) confClass = 'medium';
    
    // Snapshot preview
    const snapshotHtml = c.snapshot_url 
      ? `<img src="${c.snapshot_url}" class="preview-thumbnail" alt="Thumb" onclick="openImageWindow('${c.snapshot_url}')">`
      : `<span class="text-muted" style="font-size: 0.75rem;">None</span>`;

    // Platform and Channel logic
    const OTT_PLATFORMS = new Set(['netflix', 'youtube', 'prime_video', 'sonyliv', 'jiohotstar', 'zee5', 'ott', 'uplay']);
    
    function getPlatformDisplayName(id) {
      if (!id) return '-';
      const mapping = {
        'netflix': 'Netflix',
        'youtube': 'YouTube',
        'prime_video': 'Prime Video',
        'sonyliv': 'SonyLIV',
        'jiohotstar': 'JioHotstar',
        'zee5': 'Zee5',
        'sony_max': 'Sony Max',
        'star_gold': 'Star Gold',
        'zee_cinema': 'Zee Cinema',
        'panarmenian_tv': 'PanArmenian TV',
        'shant_tv_armenia': 'SHANT TV Armenia'
      };
      const key = id.toLowerCase().trim();
      return mapping[key] || id.replace(/[-_]+/g, ' ').replace(/\b\w/g, ch => ch.toUpperCase());
    }

    const matchedPlat = c.result && c.result.matched_platform ? c.result.matched_platform : 'unknown';
    const matchedChan = c.result && c.result.matched_channel ? c.result.matched_channel : '';

    let platformVal = 'unknown';
    let channelVal = 'unknown';

    if (matchedPlat !== 'unknown') {
      const platKey = matchedPlat.toLowerCase().trim();
      if (OTT_PLATFORMS.has(platKey)) {
        platformVal = getPlatformDisplayName(matchedPlat);
      } else {
        // It's a channel (logo matched)
        channelVal = getPlatformDisplayName(matchedPlat);
      }
    }

    if (matchedChan) {
      channelVal = matchedChan;
    }

    // Fallback: if we recognized visual content of a series, platform defaults to YouTube
    if (platformVal === 'unknown' && c.result && c.result.content_type === 'series') {
      platformVal = 'YouTube';
    }

    const platformHtml = (platformVal !== 'unknown' && platformVal !== '-')
      ? `<span class="tag platform-${platformVal.toLowerCase().replace(/[^a-z0-9]/g, '')}" style="background: rgba(167, 139, 250, 0.15); color: #c084fc; border: 1px solid rgba(167, 139, 250, 0.3); padding: 2px 8px; border-radius: 4px; font-weight: 500; font-size: 0.75rem;">${platformVal}</span>`
      : `<span class="text-muted" style="font-size: 0.8rem;">-</span>`;

    const channelHtml = (channelVal !== 'unknown' && channelVal !== '-')
      ? `<span class="tag channel-${channelVal.toLowerCase().replace(/[^a-z0-9]/g, '')}" style="background: rgba(96, 165, 250, 0.15); color: #60a5fa; border: 1px solid rgba(96, 165, 250, 0.3); padding: 2px 8px; border-radius: 4px; font-weight: 500; font-size: 0.75rem;">${channelVal}</span>`
      : `<span class="text-muted" style="font-size: 0.8rem;">-</span>`;

    // Category / Tag
    const category = c.result ? c.result.content_type : 'unknown';
    
    // Format content name with structured metadata
    let contentName = c.result ? c.result.content_name : 'Unknown Content';
    if (c.result && c.result.series) {
      let s = (c.result.season !== null && c.result.season !== undefined) ? String(c.result.season).padStart(2, '0') : '';
      let e = (c.result.episode !== null && c.result.episode !== undefined) ? String(c.result.episode).padStart(2, '0') : '';
      
      let baseName = c.result.series;
      if (s && e) {
        baseName += ` S${s}E${e}`;
      } else if (s) {
        baseName += ` S${s}`;
      } else if (e) {
        baseName += ` E${e}`;
      }
      
      let originalName = c.result.content_name || "";
      let normalize = (str) => str.toLowerCase().replace(/[^a-z0-9]/g, '').replace(/s0+/g, 's').replace(/e0+/g, 'e');
      
      if (originalName && originalName !== c.result.series && normalize(originalName) !== normalize(baseName)) {
        let regSeries = new RegExp('^' + c.result.series.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '[\\s:-]*', 'i');
        let extra = originalName.replace(regSeries, '');
        extra = extra.replace(/^s\d+\s*e\d+[\s:-]*/i, '');
        
        if (extra) {
           contentName = baseName + (extra.startsWith('(') ? ' ' : ' - ') + extra;
         } else {
           contentName = baseName;
         }
      } else {
        contentName = baseName;
      }
    }
    
    // Match breakdown
    const breakdown = c.result ? c.result.breakdown : { visual_score: 0, audio_score: 0, ocr_score: 0, logo_score: 0 };
    const breakdownText = `V: ${(breakdown.visual_score).toFixed(2)} | A: ${(breakdown.audio_score).toFixed(2)} | L: ${(breakdown.logo_score).toFixed(2)}`;

    return `
      <tr>
        <td style="font-weight: 500;">${date}</td>
        <td style="font-family: monospace; color: #a78bfa;">${c.device_id}</td>
        <td>${snapshotHtml}</td>
        <td>${platformHtml}</td>
        <td>${channelHtml}</td>
        <td style="font-weight: 600;">${contentName}</td>
        <td><span class="tag ${category}">${category}</span></td>
        <td>
          <div class="confidence-indicator">
            <div class="confidence-bar-bg">
              <div class="confidence-bar-fg ${confClass}" style="width: ${confidence * 100}%"></div>
            </div>
            <span class="confidence-text ${confClass}">${(confidence * 100).toFixed(0)}%</span>
          </div>
        </td>
        <td style="font-family: monospace; font-size: 0.75rem; color: var(--text-secondary);">${breakdownText}</td>
      </tr>
    `;
  }).join('');
}

// Render: REVIEW QUEUE
function renderReviewQueue() {
  if (state.unknowns.length === 0) {
    elements.reviewList.innerHTML = `
      <div class="loading-state" style="grid-column: 1 / -1; padding: 60px;">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="48" height="48" style="color: var(--color-success); margin-bottom: 16px; opacity: 0.8;">
          <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/>
          <polyline points="22 4 12 14.01 9 11.01"/>
        </svg>
        <h3 style="color: var(--text-primary); font-family: var(--font-heading); margin-bottom: 8px;">Review Queue Clear!</h3>
        <p style="color: var(--text-muted); font-size: 0.85rem;">All edge device captures have been recognized successfully.</p>
      </div>
    `;
    return;
  }

  elements.reviewList.innerHTML = state.unknowns.map(u => {
    const timeStr = new Date(u.captured_at).toLocaleString();
    const mediaHtml = u.snapshot_url
      ? `<img src="${u.snapshot_url}" alt="Capture Preview">`
      : `<div class="no-snapshot">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>
          No Snapshot
         </div>`;
    
    return `
      <div class="review-card">
        <div class="review-media">
          ${mediaHtml}
        </div>
        <div class="review-content">
          <div class="review-meta">
            <span>ID: #${u.id}</span>
            <span>${timeStr}</span>
          </div>
          <h4 style="margin-bottom: 8px; font-family: var(--font-heading);">Device: <span style="color: #c084fc;">${u.device_id}</span></h4>
          <div class="ocr-box" style="display: none;"></div>
          <div class="review-actions">
            <button class="btn btn-primary btn-sm" onclick="openResolveModal(${u.id})">
              Resolve Capture
            </button>
          </div>
        </div>
      </div>
    `;
  }).join('');
}

// Render: REFERENCE LIBRARY
function renderLibrary() {
  if (state.library.length === 0) {
    elements.libraryList.innerHTML = '<tr><td colspan="8" class="loading-state">Reference library is empty. Add signatures to begin matching.</td></tr>';
    return;
  }

  elements.libraryList.innerHTML = state.library.map(item => {
    const visualStr = item.visual_fp ? `[${item.visual_fp.slice(0, 4).map(v => v.toFixed(2)).join(', ')}]` : '[]';
    const logoStr = item.logo_fp ? `[${item.logo_fp.slice(0, 4).map(v => v.toFixed(2)).join(', ')}]` : '[]';
    
    return `
      <tr>
        <td style="font-family: monospace; font-size: 0.75rem; color: var(--text-muted);">${item.external_content_id}</td>
        <td style="font-weight: 600;">${item.title}</td>
        <td><span class="tag ${item.category}">${item.category}</span></td>
        <td>${item.channel_name || '<span class="text-muted">-</span>'}</td>
        <td style="font-family: monospace; color: #60a5fa;">${item.audio_fp || '-'}</td>
        <td style="display: none;">${item.ocr_keywords || '-'}</td>
        <td style="font-family: monospace; font-size: 0.75rem; color: var(--text-secondary);">
          V: ${visualStr}<br>
          L: ${logoStr}
        </td>
        <td>
          <button class="btn btn-danger btn-sm" onclick="deleteLibraryItem(${item.id})">Delete</button>
        </td>
      </tr>
    `;
  }).join('');
}

// Render: PLATFORMS REFERENCE (Channel Logos)
// Render: PLATFORMS REFERENCE (Channel Logos) - Grouped to prevent clutter
function renderPlatforms() {
  if (!state.platforms || state.platforms.length === 0) {
    elements.platformsList.innerHTML = '<tr><td colspan="5" class="loading-state">Platform database is empty. Register logos to begin matching.</td></tr>';
    return;
  }

  // Group by platform_id
  const groups = {};
  state.platforms.forEach(item => {
    const key = item.platform_id;
    if (!groups[key]) {
      groups[key] = {
        platform_id: item.platform_id,
        platform_name: item.platform_name,
        platform_type: item.platform_type,
        count: 0,
        ids: []
      };
    }
    groups[key].count++;
    groups[key].ids.push(item.id);
  });

  elements.platformsList.innerHTML = Object.values(groups).map(group => {
    return `
      <tr>
        <td style="font-family: monospace; font-size: 0.75rem; color: var(--text-muted);">${group.platform_id}</td>
        <td style="font-weight: 600; color: #a78bfa;">${group.platform_name}</td>
        <td><span class="tag ${group.platform_type.toLowerCase() === 'ott' ? 'ott' : 'channel'}" style="background: rgba(96, 165, 250, 0.15); color: #60a5fa; border: 1px solid rgba(96, 165, 250, 0.3); padding: 2px 8px; border-radius: 4px; font-weight: 500; font-size: 0.75rem;">${group.platform_type}</span></td>
        <td style="font-weight: 500; color: var(--text-secondary);">${group.count} template reference${group.count === 1 ? '' : 's'}</td>
        <td>
          <button class="btn btn-danger btn-sm" onclick="deletePlatformGroup('${group.platform_id}', [${group.ids.join(', ')}])">Delete All</button>
        </td>
      </tr>
    `;
  }).join('');
}

window.deletePlatformGroup = async function(platformId, ids) {
  if (!confirm(`Are you sure you want to delete all ${ids.length} templates for platform "${platformId}"?`)) return;

  try {
    const response = await fetch(`/api/v1/platforms?platform_id=${encodeURIComponent(platformId)}`, {
      method: 'DELETE'
    });
    if (response.ok) {
      const data = await response.json();
      showToast('Reference Removed', `Successfully deleted all ${data.count || ids.length} templates for "${platformId}".`, 'success');
      fetchData();
    } else {
      showToast('Error', 'Failed to delete templates.', 'danger');
    }
  } catch (e) {
    console.error('Failed to delete templates:', e);
    showToast('Error', 'Failed to delete templates.', 'danger');
  }
};

window.deletePlatformItem = async function(itemId) {
  if (!confirm('Are you sure you want to delete this channel template reference?')) return;

  try {
    const response = await fetch(`/api/v1/platforms/${itemId}`, {
      method: 'DELETE'
    });

    if (response.ok) {
      showToast('Reference Removed', 'The channel template has been deleted.', 'success');
      fetchData();
    } else {
      showToast('Error', 'Failed to delete channel template.', 'danger');
    }
  } catch (error) {
    console.error('Delete channel template error:', error);
    showToast('Connection Error', 'Server communication failure.', 'danger');
  }
};

// Render: CONNECTED DEVICES
function renderDevices() {
  if (state.devices.length === 0) {
    elements.devicesList.innerHTML = '<tr><td colspan="6" class="loading-state">No connected devices detected.</td></tr>';
    return;
  }

  elements.devicesList.innerHTML = state.devices.map(d => {
    const statusClass = d.status === 'active' ? 'online' : 'offline';
    const lastActiveStr = d.last_active ? new Date(d.last_active).toLocaleString() : 'Never';
    return `
      <tr>
        <td style="font-family: monospace; font-weight: 600; color: #a78bfa;">${d.device_id}</td>
        <td>
          <div class="system-status" style="display: inline-flex; background: transparent; border: none; padding: 0;">
            <span class="status-indicator ${statusClass}" style="background-color: ${d.status === 'active' ? 'var(--color-success)' : 'var(--text-muted)'}; box-shadow: ${d.status === 'active' ? '0 0 8px var(--color-success)' : 'none'};"></span>
            <span class="status-text" style="color: ${d.status === 'active' ? 'var(--color-success)' : 'var(--text-muted)'}; text-transform: capitalize; margin-left: 8px;">${d.status}</span>
          </div>
        </td>
        <td>${d.location || '<span class="text-muted">Not Set</span>'}</td>
        <td style="font-weight: 500;">${d.capture_count}</td>
        <td>${lastActiveStr}</td>
        <td>
          <button class="btn btn-danger btn-sm" onclick="deleteDevice('${d.device_id}')">Delete</button>
        </td>
      </tr>
    `;
  }).join('');
}

window.deleteDevice = async function(deviceId) {
  if (!confirm(`Are you sure you want to delete device "${deviceId}"? This will also delete all captures and history associated with this device.`)) return;

  try {
    const response = await fetch(`/api/v1/devices/${encodeURIComponent(deviceId)}`, {
      method: 'DELETE'
    });

    if (response.ok) {
      showToast('Device Deleted', `Successfully deleted device "${deviceId}" and its history.`, 'success');
      fetchData();
    } else {
      showToast('Error', 'Failed to delete device.', 'danger');
    }
  } catch (error) {
    console.error('Delete device error:', error);
    showToast('Connection Error', 'Server communication failure.', 'danger');
  }
};

// Open Resolve Capture Modal
function openResolveModal(captureId) {
  const capture = state.unknowns.find(u => u.id === captureId);
  if (!capture) return;

  elements.resolveCaptureId.value = captureId;
  elements.resolveImg.src = capture.snapshot_url || '';
  elements.resolveImg.style.display = capture.snapshot_url ? 'block' : 'none';
  elements.resolveOcr.textContent = capture.ocr_text || '(No OCR text extracted)';
  elements.resolveAudio.textContent = capture.audio_fp || '(No Audio signature detected)';
  
  // Clear inputs
  elements.resolveForm.reset();
  
  elements.resolveModal.classList.add('active');
  stopSSE(); // Pause background polling
}

function hideResolveModal() {
  elements.resolveModal.classList.remove('active');
  if (state.isPolling) startSSE(); // Resume if enabled
}

// Handle Resolve Modal Submission
async function handleResolveSubmit(e) {
  e.preventDefault();
  const captureId = elements.resolveCaptureId.value;
  const title = document.getElementById('resolve-title').value;
  const category = document.getElementById('resolve-category').value;
  const channel = document.getElementById('resolve-channel').value;

  try {
    const response = await fetch(`/api/v1/captures/${captureId}/resolve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: title,
        category: category,
        channel_name: channel || null
      })
    });

    if (response.ok) {
      showToast('Capture Resolved', `"${title}" has been added to content library!`, 'success');
      hideResolveModal();
      fetchData(); // Trigger reload
    } else {
      showToast('Error', 'Failed to resolve capture.', 'danger');
    }
  } catch (error) {
    console.error('Resolve submit error:', error);
    showToast('Connection Error', 'Failed to resolve capture.', 'danger');
  }
}

// Add Library Modal Controls
function showAddLibraryModal() {
  elements.addLibraryForm.reset();
  elements.addLibraryModal.classList.add('active');
  stopSSE();
}

function hideAddLibraryModal() {
  elements.addLibraryModal.classList.remove('active');
  if (state.isPolling) startSSE();
}

// Handle Add Library Modal Submission
async function handleAddLibrarySubmit(e) {
  e.preventDefault();
  const title = document.getElementById('lib-title').value;
  const category = document.getElementById('lib-category').value;
  const channel = document.getElementById('lib-channel').value;
  const audio = document.getElementById('lib-audio').value;
  const ocr = document.getElementById('lib-ocr').value;
  
  const visualVal = document.getElementById('lib-visual').value.split(',').map(n => parseFloat(n.trim()));
  const logoVal = document.getElementById('lib-logo').value.split(',').map(n => parseFloat(n.trim()));

  if (visualVal.length !== 4 || visualVal.some(isNaN) || logoVal.length !== 4 || logoVal.some(isNaN)) {
    showToast('Validation Error', 'Fingerprints must contain exactly 4 comma-separated float numbers.', 'danger');
    return;
  }

  try {
    const response = await fetch('/api/v1/library', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title,
        category,
        channel_name: channel || null,
        visual_fp: visualVal,
        audio_fp: audio,
        logo_fp: logoVal,
        ocr_keywords: ocr
      })
    });

    if (response.ok) {
      showToast('Library Updated', `"${title}" reference signature saved.`, 'success');
      hideAddLibraryModal();
      fetchData();
    } else {
      showToast('Error', 'Failed to save signature.', 'danger');
    }
  } catch (error) {
    console.error('Add library signature error:', error);
    showToast('Connection Error', 'Server communication failure.', 'danger');
  }
}

// Delete Library Signature
async function deleteLibraryItem(itemId) {
  if (!confirm('Are you sure you want to delete this reference content signature?')) return;

  try {
    const response = await fetch(`/api/v1/library/${itemId}`, {
      method: 'DELETE'
    });

    if (response.ok) {
      showToast('Signature Removed', 'The library entry has been deleted.', 'success');
      fetchData();
    } else {
      showToast('Error', 'Failed to delete signature.', 'danger');
    }
  } catch (error) {
    console.error('Delete signature error:', error);
    showToast('Connection Error', 'Server communication failure.', 'danger');
  }
}

// Helper: Open image in a new tab / viewport
window.openImageWindow = function(url) {
  window.open(url, '_blank', 'noopener,noreferrer');
};

// Render: ANALYTICS (Charts & Timeline)
async function renderAnalytics() {
  try {
    const [overview, share, ads, sessions] = await Promise.all([
      fetch('/api/v1/analytics/overview').then(r => r.json()),
      fetch('/api/v1/analytics/share').then(r => r.json()),
      fetch('/api/v1/analytics/ad-frequency').then(r => r.json()),
      fetch('/api/v1/analytics/sessions').then(r => r.json())
    ]);

    // Update charts
    renderShareChart(share);
    renderAdChart(ads);
    renderSessions(sessions);
  } catch (error) {
    console.error('Error rendering analytics tab:', error);
    showToast('Analytics Error', 'Failed to retrieve charts data.', 'danger');
  }
}

function renderShareChart(shareData) {
  const canvas = document.getElementById('share-chart');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (shareChart) shareChart.destroy();

  const categories = Object.keys(shareData);
  const counts = Object.values(shareData);
  
  if (categories.length === 0) {
    ctx.font = '14px Plus Jakarta Sans';
    ctx.fillStyle = '#64748b';
    ctx.textAlign = 'center';
    ctx.fillText('No category data available', canvas.width / 2, canvas.height / 2);
    return;
  }

  const bgColors = categories.map(cat => {
    switch (cat.toLowerCase()) {
      case 'channel': return '#8b5cf6';
      case 'movie': return '#3b82f6';
      case 'advertisement': return '#f97316';
      case 'series': case 'episode': return '#ec4899';
      case 'ott': return '#06b6d4';
      default: return '#ef4444';
    }
  });

  shareChart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: categories.map(c => c.toUpperCase()),
      datasets: [{
        data: counts,
        backgroundColor: bgColors,
        borderWidth: 1,
        borderColor: 'rgba(255, 255, 255, 0.08)'
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'right',
          labels: {
            color: '#94a3b8',
            font: { family: 'Plus Jakarta Sans', size: 10 }
          }
        }
      }
    }
  });
}

function renderAdChart(adData) {
  const canvas = document.getElementById('ad-chart');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (adChart) adChart.destroy();

  if (adData.length === 0) {
    ctx.font = '14px Plus Jakarta Sans';
    ctx.fillStyle = '#64748b';
    ctx.textAlign = 'center';
    ctx.fillText('No advertisement data available', canvas.width / 2, canvas.height / 2);
    return;
  }

  const adNames = adData.map(ad => ad.name);
  const adCounts = adData.map(ad => ad.count);

  adChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: adNames,
      datasets: [{
        label: 'Detections',
        data: adCounts,
        backgroundColor: 'rgba(139, 92, 246, 0.4)',
        borderColor: '#8b5cf6',
        borderWidth: 1,
        borderRadius: 4
      }]
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false }
      },
      scales: {
        x: {
          ticks: { color: '#94a3b8', font: { family: 'Plus Jakarta Sans', size: 10 } },
          grid: { color: 'rgba(255, 255, 255, 0.05)' }
        },
        y: {
          ticks: { color: '#94a3b8', font: { family: 'Plus Jakarta Sans', size: 10 } },
          grid: { display: false }
        }
      }
    }
  });
}

function renderTimeline(items) {
  const container = document.getElementById('overview-timeline');
  if (!container) return;

  if (!items || items.length === 0) {
    container.innerHTML = '<div class="loading-state">No playback history recorded yet.</div>';
    return;
  }

  let html = `<div class="timeline-container" style="display: flex; flex-direction: column; gap: 16px; position: relative; padding-left: 24px; border-left: 2px solid var(--border-glass); margin-left: 10px;">`;

  items.forEach(item => {
    const timeStr = new Date(item.timestamp).toLocaleString();
    html += `
      <div class="timeline-item" style="position: relative;">
        <div class="timeline-dot" style="position: absolute; left: -31px; top: 4px; width: 12px; height: 12px; border-radius: 50%; background: var(--color-primary); border: 2px solid var(--bg-secondary); box-shadow: 0 0 6px var(--color-primary);"></div>
        <div style="background: rgba(255,255,255,0.02); border: 1px solid var(--border-glass); padding: 12px 16px; border-radius: 8px; display: flex; justify-content: space-between; align-items: center;">
          <div>
            <span style="font-weight: 600; font-size: 0.95rem; color: var(--text-primary);">${item.content_name}</span>
            <span class="tag ${item.category}" style="margin-left: 10px;">${item.category}</span>
          </div>
          <div style="text-align: right;">
            <span style="font-size: 0.8rem; color: var(--text-secondary);">${timeStr}</span>
            <span style="display: block; font-size: 0.75rem; color: var(--text-muted); font-family: monospace; margin-top: 4px;">Device: ${item.device_id}</span>
          </div>
        </div>
      </div>
    `;
  });

  html += '</div>';
  container.innerHTML = html;
}

function renderSessions(items) {
  const container = document.getElementById('analytics-sessions');
  if (!container) return;

  if (!items || items.length === 0) {
    container.innerHTML = '<div class="loading-state">No playback sessions recorded yet.</div>';
    return;
  }

  let html = `<div class="timeline-container" style="display: flex; flex-direction: column; gap: 16px; position: relative; padding-left: 24px; border-left: 2px solid var(--border-glass); margin-left: 10px;">`;

  items.forEach(item => {
    const startStr = new Date(item.start_time).toLocaleString();
    const endStr = new Date(item.end_time).toLocaleTimeString();
    const duration = Math.round(item.duration_seconds);
    html += `
      <div class="timeline-item" style="position: relative;">
        <div class="timeline-dot" style="position: absolute; left: -31px; top: 4px; width: 12px; height: 12px; border-radius: 50%; background: var(--color-primary); border: 2px solid var(--bg-secondary); box-shadow: 0 0 6px var(--color-primary);"></div>
        <div style="background: rgba(255,255,255,0.02); border: 1px solid var(--border-glass); padding: 12px 16px; border-radius: 8px; display: flex; justify-content: space-between; align-items: center;">
          <div>
            <span style="font-weight: 600; font-size: 0.95rem; color: var(--text-primary);">${item.content_name}</span>
            <span class="tag ${item.category}" style="margin-left: 10px;">${item.category}</span>
            <span style="display: block; font-size: 0.8rem; color: var(--text-muted); margin-top: 4px;">
              Played for: <strong>${duration}s</strong> (${item.entry_count} consecutive detections)
            </span>
          </div>
          <div style="text-align: right;">
            <span style="font-size: 0.8rem; color: var(--text-secondary);">${startStr} - ${endStr}</span>
            <span style="display: block; font-size: 0.75rem; color: var(--text-muted); font-family: monospace; margin-top: 4px;">Device: ${item.device_id}</span>
          </div>
        </div>
      </div>
    `;
  });

  html += '</div>';
  container.innerHTML = html;
}


// UI Notification Toasts Creator
function showToast(title, message, type = 'info') {
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `
    <div class="toast-title">${title}</div>
    <div class="toast-message">${message}</div>
  `;
  
  elements.toastContainer.appendChild(toast);
  
  // Animate slide-out and remove
  setTimeout(() => {
    toast.style.transform = 'translateX(120%)';
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// Fetch and render library items inside platforms tab
// Fetch and render library items inside platforms tab, grouped by series and collapsible
async function loadAndRenderLibraryPlat() {
  const container = document.getElementById('library-plat-container');
  const tbody = document.getElementById('library-plat-list');
  const btn = document.getElementById('btn-load-library-plat');
  if (!container || !tbody || !btn) return;

  btn.disabled = true;
  btn.textContent = 'Loading...';

  try {
    const res = await fetch('/api/v1/library');
    if (!res.ok) throw new Error('Failed to fetch library');
    const library = await res.json();
    
    if (library.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" class="loading-state">No content library items found.</td></tr>';
    } else {
      // Group by series or category
      const groups = {};
      library.forEach(item => {
        let groupKey = item.series;
        if (!groupKey) {
          if (item.title && item.title.toLowerCase().includes('intro')) {
            groupKey = 'Standalone Intros';
          } else {
            groupKey = 'Standalone Entries';
          }
        }
        
        if (!groups[groupKey]) {
          groups[groupKey] = {
            name: groupKey,
            category: item.category,
            platform: item.platform || item.channel_name || '-',
            items: []
          };
        }
        groups[groupKey].items.push(item);
      });

      let html = '';
      Object.values(groups).forEach((group, idx) => {
        const groupId = `lib-group-${idx}`;
        const totalCount = group.items.length;
        const countText = totalCount === 1 ? '1 item' : `${totalCount} items`;
        
        // Parent Header Row
        html += `
          <tr class="group-header-row" style="cursor: pointer; background: rgba(255,255,255,0.02);" onclick="toggleLibGroup('${groupId}')">
            <td style="font-weight: 700; color: #c084fc;">
              <span class="chevron" id="${groupId}-chevron" style="display: inline-block; transition: transform 0.2s; margin-right: 8px;">▸</span>
              ${group.name}
            </td>
            <td style="font-weight: 600; color: var(--text-secondary);">${countText}</td>
            <td><span class="tag" style="background: rgba(167, 139, 250, 0.15); color: #c084fc; border: 1px solid rgba(167, 139, 250, 0.3); padding: 2px 8px; border-radius: 4px; font-weight: 500; font-size: 0.75rem;">${group.category}</span></td>
            <td style="color: var(--text-muted);">${group.platform}</td>
          </tr>
        `;
        
        // Children Rows
        group.items.forEach(item => {
          html += `
            <tr class="${groupId}-child" style="display: none; background: rgba(0,0,0,0.15);">
              <td style="padding-left: 28px; font-weight: 500; color: var(--text-secondary);">${item.title}</td>
              <td style="font-family: monospace; font-size: 0.7rem; color: var(--text-muted);">${item.external_content_id}</td>
              <td><span class="tag" style="background: rgba(255,255,255,0.05); color: var(--text-secondary); border: 1px solid rgba(255,255,255,0.1); padding: 2px 8px; border-radius: 4px; font-size: 0.7rem;">${item.category}</span></td>
              <td style="font-size: 0.8rem; color: var(--text-muted);">${item.platform || item.channel_name || '-'}</td>
            </tr>
          `;
        });
      });

      tbody.innerHTML = html;
    }
    
    container.style.display = 'block';
    btn.textContent = 'Refresh Content Library';
  } catch (error) {
    console.error('Error loading library for platform tab:', error);
    showToast('Error', 'Failed to load content library.', 'danger');
    btn.textContent = 'Retry Loading Library';
  } finally {
    btn.disabled = false;
  }
}

// Global toggle helper for expanding content groups
window.toggleLibGroup = function(groupId) {
  const children = document.querySelectorAll(`.${groupId}-child`);
  const chevron = document.getElementById(`${groupId}-chevron`);
  if (!children.length) return;

  const isHidden = children[0].style.display === 'none';
  children.forEach(child => {
    child.style.display = isHidden ? 'table-row' : 'none';
  });

  if (chevron) {
    chevron.style.transform = isHidden ? 'rotate(90deg)' : 'rotate(0deg)';
  }
};
