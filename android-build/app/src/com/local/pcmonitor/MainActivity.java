package com.local.pcmonitor;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.FrameLayout;

import java.net.HttpURLConnection;
import java.net.URL;

/**
 * 电脑资源监控 · 局域网客户端
 * - 强制横屏 + 沉浸式全屏（无状态栏）
 * - 主机服务未启动时显示本地时钟 / 日历页，每 5 秒自动探测重连
 * - 长按屏幕可修改服务器地址
 */
public class MainActivity extends Activity {

    private static final String PREFS = "pc_monitor";
    private static final String KEY_URL = "server_url";
    private static final String DEFAULT_URL = "http://192.168.1.107:8000";
    private static final String CLOCK_PAGE = "file:///android_asset/clock.html";
    private static final int BG = 0xFF080C11;
    private static final int RETRY_MS = 5000;
    private static final int WEATHER_MS = 15 * 60 * 1000;   // 天气 15 分钟刷新一次

    // 天气源（Open-Meteo 免密钥）。城市坐标按家庭宽带出口位置预设（南京）
    private static final String WEATHER_URL =
        "https://api.open-meteo.com/v1/forecast?latitude=32.06&longitude=118.78"
        + "&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m"
        + "&daily=temperature_2m_max,temperature_2m_min"
        + "&timezone=Asia%2FShanghai&forecast_days=1";

    private WebView web;
    private String url;
    private boolean showingClock = false;
    private boolean probing = false;
    private boolean destroyed = false;
    private final Handler handler = new Handler(Looper.getMainLooper());

    private final Runnable retryTask = new Runnable() {
        @Override public void run() {
            if (!probing) probe();
        }
    };

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        SharedPreferences sp = getSharedPreferences(PREFS, MODE_PRIVATE);
        url = sp.getString(KEY_URL, DEFAULT_URL);

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(BG);

        web = new WebView(this);
        web.setBackgroundColor(BG);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setSupportZoom(false);
        s.setBuiltInZoomControls(false);
        s.setDisplayZoomControls(false);
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);
        s.setUserAgentString(s.getUserAgentString() + " PCMonitorApp/1.1");
        if (Build.VERSION.SDK_INT >= 21) {
            s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        }
        web.addJavascriptInterface(new Bridge(), "PCMBridge");

        web.setWebViewClient(new WebViewClient() {
            @Override public void onPageFinished(WebView view, String u) {
                if (u != null && u.startsWith("file:")) {
                    showingClock = true;
                    scheduleRetry();
                    scheduleWeather();
                } else {
                    showingClock = false;
                }
            }

            @Override public void onReceivedError(WebView view, int code, String desc, String failingUrl) {
                if (failingUrl != null && failingUrl.startsWith("http")) showClock();
            }

            @Override public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (Build.VERSION.SDK_INT >= 23 && req != null && req.isForMainFrame()) showClock();
            }
        });

        // 长按屏幕任意位置可修改服务器地址（时钟页同样有效）
        web.setOnLongClickListener(new View.OnLongClickListener() {
            @Override public boolean onLongClick(View v) {
                showUrlDialog();
                return true;
            }
        });

        root.addView(web, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);
        loadDashboard();
    }

    /** JS 回调：页面自己检测到连续断连时，切到时钟页 */
    private class Bridge {
        @JavascriptInterface
        public void onOffline() {
            runOnUiThread(new Runnable() {
                @Override public void run() { showClock(); }
            });
        }
    }

    private void loadDashboard() {
        showingClock = false;
        if (web != null) web.loadUrl(url);
    }

    private void showClock() {
        if (destroyed) return;
        runOnUiThread(new Runnable() {
            @Override public void run() {
                if (showingClock) {
                    scheduleRetry();
                    return;
                }
                showingClock = true;
                if (web != null) web.loadUrl(CLOCK_PAGE);
                scheduleRetry();
                scheduleWeather();
            }
        });
    }

    /** 时钟页可见时定时刷新天气 */
    private void scheduleWeather() {
        handler.removeCallbacks(weatherTask);
        handler.postDelayed(weatherTask, 900);
    }

    /** 时钟页可见时定时刷新天气 */
    private final Runnable weatherTask = new Runnable() {
        @Override public void run() {
            if (destroyed || !showingClock) return;
            fetchWeather();
            handler.postDelayed(this, WEATHER_MS);
        }
    };

    /** 原生层直接请求天气（绕开 WebView 的跨域限制），注入给时钟页渲染 */
    private void fetchWeather() {
        final String url = WEATHER_URL;
        new Thread(new Runnable() {
            @Override public void run() {
                String body = null;
                HttpURLConnection conn = null;
                try {
                    conn = (HttpURLConnection) new URL(url).openConnection();
                    conn.setConnectTimeout(8000);
                    conn.setReadTimeout(8000);
                    conn.setRequestMethod("GET");
                    conn.setUseCaches(false);
                    if (conn.getResponseCode() == 200) {
                        java.io.InputStream in = conn.getInputStream();
                        java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream();
                        byte[] buf = new byte[4096];
                        int n;
                        while ((n = in.read(buf)) > 0) bos.write(buf, 0, n);
                        in.close();
                        body = bos.toString("UTF-8");
                    }
                } catch (Exception ignore) {
                    body = null;
                } finally {
                    if (conn != null) conn.disconnect();
                }
                if (body == null || destroyed) return;
                final String json = body;
                handler.post(new Runnable() {
                    @Override public void run() {
                        if (destroyed || !showingClock || web == null) return;
                        web.evaluateJavascript("PCM_WEATHER(" + json + ")", null);
                    }
                });
            }
        }).start();
    }

    private void scheduleRetry() {
        handler.removeCallbacks(retryTask);
        if (!destroyed) handler.postDelayed(retryTask, RETRY_MS);
    }

    /** 轻量探测：不打扰当前页面，确认服务恢复后再加载看板 */
    private void probe() {
        probing = true;
        new Thread(new Runnable() {
            @Override public void run() {
                boolean ok = false;
                HttpURLConnection conn = null;
                try {
                    conn = (HttpURLConnection) new URL(url + "/api/stats").openConnection();
                    conn.setConnectTimeout(1800);
                    conn.setReadTimeout(1800);
                    conn.setRequestMethod("GET");
                    conn.setUseCaches(false);
                    ok = conn.getResponseCode() == 200;
                } catch (Exception ignore) {
                    ok = false;
                } finally {
                    if (conn != null) conn.disconnect();
                }
                final boolean reachable = ok;
                handler.post(new Runnable() {
                    @Override public void run() {
                        probing = false;
                        if (destroyed) return;
                        if (reachable) loadDashboard();
                        else scheduleRetry();
                    }
                });
            }
        }).start();
    }

    private void showUrlDialog() {
        final EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        input.setText(url);
        input.setSelection(input.getText().length());
        input.setHint(DEFAULT_URL);

        new AlertDialog.Builder(this)
            .setTitle("电脑监控地址")
            .setMessage("填写电脑启动服务后显示的地址（如 http://192.168.1.107:8000）")
            .setView(input)
            .setPositiveButton("保存并连接", new DialogInterface.OnClickListener() {
                @Override public void onClick(DialogInterface d, int w) {
                    String v = input.getText().toString().trim();
                    if (v.length() == 0) return;
                    if (!v.startsWith("http://") && !v.startsWith("https://")) v = "http://" + v;
                    while (v.endsWith("/")) v = v.substring(0, v.length() - 1);
                    url = v;
                    getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(KEY_URL, url).apply();
                    loadDashboard();
                }
            })
            .setNegativeButton("取消", null)
            .show();
    }

    // ---------------- 沉浸式全屏 ----------------
    private void hideSystemBars() {
        View decor = getWindow().getDecorView();
        if (Build.VERSION.SDK_INT >= 30) {
            getWindow().setDecorFitsSystemWindows(false);
            WindowInsetsController c = getWindow().getInsetsController();
            if (c != null) {
                c.hide(WindowInsets.Type.statusBars() | WindowInsets.Type.navigationBars());
                c.setSystemBarsBehavior(
                    WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
            }
        } else {
            decor.setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_FULLSCREEN
                | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY);
        }
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) hideSystemBars();
    }

    @Override
    protected void onResume() {
        super.onResume();
        hideSystemBars();
        if (web != null && !showingClock) web.loadUrl(url);
        else scheduleRetry();
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK) {
            finish();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    protected void onDestroy() {
        destroyed = true;
        handler.removeCallbacksAndMessages(null);
        if (web != null) {
            web.loadUrl("about:blank");
            web.destroy();
        }
        super.onDestroy();
    }
}
