package com.local.pcmonitor;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.DialogInterface;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.location.Address;
import android.location.Geocoder;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
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
import android.webkit.GeolocationPermissions;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.FrameLayout;

import java.net.HttpURLConnection;
import java.net.URL;
import java.util.List;
import java.util.Locale;

/**
 * 电脑资源监控 · 局域网客户端
 * - 强制横屏 + 沉浸式全屏（无状态栏）
 * - 主机服务未启动时显示本地时钟页：时间 / 日期（含农历）/ 星期 / 天气，每 5 秒自动探测重连
 * - 天气按手机位置服务（GPS / 网络定位）取当前位置，不再写死城市
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
    private static final int REQ_LOCATION = 1001;

    private static final String KEY_LAT = "wx_lat";
    private static final String KEY_LON = "wx_lon";
    private static final String KEY_CITY = "wx_city";
    // 尚未定位成功时的兜底坐标，拿到真实位置后立即替换
    private static final double FALLBACK_LAT = 32.06;
    private static final double FALLBACK_LON = 118.78;

    private WebView web;
    private String url;
    private boolean showingClock = false;
    private boolean probing = false;
    private boolean destroyed = false;
    private final Handler handler = new Handler(Looper.getMainLooper());

    // 位置与城市（wxLat 为 NaN 表示还没定位）
    private double wxLat = Double.NaN;
    private double wxLon = Double.NaN;
    private String wxCity = null;
    private String wxSource = "";
    private LocationManager locMgr;
    private LocationListener locListener;

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
        restoreLocation();

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(BG);

        web = new WebView(this);
        web.setBackgroundColor(BG);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setGeolocationEnabled(true);           // 允许页面 navigator.geolocation（兜底路径）
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setSupportZoom(false);
        s.setBuiltInZoomControls(false);
        s.setDisplayZoomControls(false);
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);
        s.setUserAgentString(s.getUserAgentString() + " PCMonitorApp/1.3");
        if (Build.VERSION.SDK_INT >= 21) {
            s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        }
        web.addJavascriptInterface(new Bridge(), "PCMBridge");

        web.setWebChromeClient(new WebChromeClient() {
            @Override public void onGeolocationPermissionsShowPrompt(String origin,
                    GeolocationPermissions.Callback callback) {
                // 位置权限已在原生层申请，这里放行页面的定位请求
                callback.invoke(origin, true, false);
            }
        });

        web.setWebViewClient(new WebViewClient() {
            @Override public void onPageFinished(WebView view, String u) {
                if (u != null && u.startsWith("file:")) {
                    showingClock = true;
                    injectLocation();
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
        requestLocationIfNeeded();
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

    // ---------------- 手机位置服务 ----------------

    /** 启动时先用上次定位，避免等待 GPS 期间天气空白 */
    private void restoreLocation() {
        SharedPreferences sp = getSharedPreferences(PREFS, MODE_PRIVATE);
        try {
            String la = sp.getString(KEY_LAT, null);
            String lo = sp.getString(KEY_LON, null);
            if (la != null && lo != null) {
                wxLat = Double.parseDouble(la);
                wxLon = Double.parseDouble(lo);
            }
        } catch (Exception ignore) {
            wxLat = Double.NaN;
            wxLon = Double.NaN;
        }
        wxCity = sp.getString(KEY_CITY, null);
    }

    private void requestLocationIfNeeded() {
        if (Build.VERSION.SDK_INT >= 23) {
            boolean granted =
                checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
                || checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED;
            if (!granted) {
                requestPermissions(new String[]{
                    Manifest.permission.ACCESS_FINE_LOCATION,
                    Manifest.permission.ACCESS_COARSE_LOCATION}, REQ_LOCATION);
                return;
            }
        }
        startLocation();
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode != REQ_LOCATION) return;
        boolean granted = false;
        if (results != null) {
            for (int r : results) {
                if (r == PackageManager.PERMISSION_GRANTED) { granted = true; break; }
            }
        }
        if (granted) {
            startLocation();
        } else {
            wxSource = "定位未授权 · 用默认位置";
            injectLocation();
        }
    }

    /** 取最近一次已知位置 + 请求一次实时定位（拿到第一个结果就停止，省电） */
    private void startLocation() {
        try {
            locMgr = (LocationManager) getSystemService(Context.LOCATION_SERVICE);
            if (locMgr == null) return;

            String[] providers = {LocationManager.NETWORK_PROVIDER,
                                  LocationManager.GPS_PROVIDER,
                                  LocationManager.PASSIVE_PROVIDER};
            Location best = null;
            for (String p : providers) {
                try {
                    if (!locMgr.isProviderEnabled(p)) continue;
                    Location l = locMgr.getLastKnownLocation(p);
                    if (l != null && (best == null || l.getTime() > best.getTime())) best = l;
                } catch (SecurityException ignore) {}
            }
            if (best != null) applyLocation(best, "上次定位");

            String provider = null;
            if (locMgr.isProviderEnabled(LocationManager.NETWORK_PROVIDER)) {
                provider = LocationManager.NETWORK_PROVIDER;
            } else if (locMgr.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
                provider = LocationManager.GPS_PROVIDER;
            }
            if (provider == null) {
                if (Double.isNaN(wxLat)) {
                    wxSource = "定位服务未开启";
                    injectLocation();
                }
                return;
            }

            stopLocationUpdates();
            locListener = new LocationListener() {
                @Override public void onLocationChanged(Location l) {
                    applyLocation(l, providerLabel(l.getProvider()));
                    stopLocationUpdates();
                }
                @Override public void onStatusChanged(String p, int status, Bundle extras) {}
                @Override public void onProviderEnabled(String p) {}
                @Override public void onProviderDisabled(String p) {}
            };
            locMgr.requestLocationUpdates(provider, 1000, 10, locListener, Looper.getMainLooper());
            handler.postDelayed(new Runnable() {
                @Override public void run() { stopLocationUpdates(); }
            }, 15000);
        } catch (SecurityException ignore) {
            wxSource = "定位不可用";
            injectLocation();
        }
    }

    private static String providerLabel(String p) {
        if (LocationManager.GPS_PROVIDER.equals(p)) return "GPS 定位";
        if (LocationManager.NETWORK_PROVIDER.equals(p)) return "网络定位";
        return "手机定位";
    }

    private void stopLocationUpdates() {
        if (locMgr != null && locListener != null) {
            try { locMgr.removeUpdates(locListener); } catch (Exception ignore) {}
        }
        locListener = null;
    }

    private void applyLocation(final Location l, String source) {
        if (l == null || destroyed) return;
        wxLat = l.getLatitude();
        wxLon = l.getLongitude();
        wxSource = source;
        getSharedPreferences(PREFS, MODE_PRIVATE).edit()
            .putString(KEY_LAT, Double.toString(wxLat))
            .putString(KEY_LON, Double.toString(wxLon))
            .apply();
        injectLocation();
        resolveCity(l);
        fetchWeather();                       // 用新坐标立刻刷新一次天气
    }

    /** 反查城市名：先用系统 Geocoder，失败再试一次免密钥的反向地理编码 */
    private void resolveCity(final Location l) {
        new Thread(new Runnable() {
            @Override public void run() {
                String city = null;
                try {
                    Geocoder g = new Geocoder(MainActivity.this, Locale.CHINA);
                    List<Address> list = g.getFromLocation(l.getLatitude(), l.getLongitude(), 1);
                    if (list != null && !list.isEmpty()) {
                        Address a = list.get(0);
                        if (a.getLocality() != null) city = a.getLocality();
                        else if (a.getSubAdminArea() != null) city = a.getSubAdminArea();
                        else if (a.getAdminArea() != null) city = a.getAdminArea();
                    }
                } catch (Exception ignore) {}
                if (city == null || city.length() == 0) {
                    city = reverseGeocode(l.getLatitude(), l.getLongitude());
                }
                if (city == null || city.length() == 0) {
                    city = osmCity(l.getLatitude(), l.getLongitude());
                }
                if (city == null || city.length() == 0 || destroyed) return;
                final String result = city;
                handler.post(new Runnable() {
                    @Override public void run() {
                        wxCity = result;
                        getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                            .putString(KEY_CITY, result).apply();
                        injectLocation();
                    }
                });
            }
        }).start();
    }

    /** 免密钥反向地理编码（Geocoder 在无 Google 服务的机器上常失败） */
    private static String reverseGeocode(double lat, double lon) {
        HttpURLConnection conn = null;
        try {
            String u = "https://api.bigdatacloud.net/data/reverse-geocode-client?latitude="
                + lat + "&longitude=" + lon + "&localityLanguage=zh";
            conn = (HttpURLConnection) new URL(u).openConnection();
            conn.setConnectTimeout(4000);
            conn.setReadTimeout(4000);
            conn.setRequestMethod("GET");
            conn.setUseCaches(false);
            if (conn.getResponseCode() != 200) return null;
            java.io.InputStream in = conn.getInputStream();
            java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream();
            byte[] buf = new byte[2048];
            int n;
            while ((n = in.read(buf)) > 0) bos.write(buf, 0, n);
            in.close();
            String json = bos.toString("UTF-8");
            String city = jsonString(json, "city");
            if (city == null || city.length() == 0) city = jsonString(json, "locality");
            if (city == null || city.length() == 0) city = jsonString(json, "principalSubdivision");
            return city;
        } catch (Exception ignore) {
            return null;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    /** OSM Nominatim 反向地理编码：最后兜底，尽量保证能显示出城市名 */
    private static String osmCity(double lat, double lon) {
        HttpURLConnection conn = null;
        try {
            String u = "https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat="
                + lat + "&lon=" + lon + "&zoom=10&accept-language=zh-CN";
            conn = (HttpURLConnection) new URL(u).openConnection();
            conn.setConnectTimeout(5000);
            conn.setReadTimeout(5000);
            conn.setRequestMethod("GET");
            conn.setUseCaches(false);
            conn.setRequestProperty("User-Agent", "PCMonitor/1.3 (offline clock widget)");
            if (conn.getResponseCode() != 200) return null;
            java.io.InputStream in = conn.getInputStream();
            java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream();
            byte[] buf = new byte[2048];
            int n;
            while ((n = in.read(buf)) > 0) bos.write(buf, 0, n);
            in.close();
            String json = bos.toString("UTF-8");
            String[] keys = {"city", "town", "village", "county", "state_district", "state"};
            for (String k : keys) {
                String v = jsonString(json, k);
                if (v != null && v.length() > 0) return v;
            }
            return null;
        } catch (Exception ignore) {
            return null;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    /** 极简 JSON 取字符串字段，避免引入第三方库 */
    private static String jsonString(String s, String key) {
        String k = "\"" + key + "\"";
        int i = s.indexOf(k);
        if (i < 0) return null;
        int c = s.indexOf(':', i + k.length());
        if (c < 0) return null;
        int q1 = s.indexOf('"', c + 1);
        if (q1 < 0) return null;
        int q2 = s.indexOf('"', q1 + 1);
        if (q2 < 0) return null;
        return s.substring(q1 + 1, q2).replace("\\/", "/");
    }

    private static String jsQuote(String s) {
        if (s == null) return "null";
        return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"")
                       .replace("\n", "\\n").replace("\r", "") + "\"";
    }

    /** 把当前坐标 / 城市推给时钟页 */
    private void injectLocation() {
        if (destroyed || web == null || !showingClock) return;
        final String latJs = Double.isNaN(wxLat) ? "null" : Double.toString(wxLat);
        final String lonJs = Double.isNaN(wxLon) ? "null" : Double.toString(wxLon);
        final String cityJs = jsQuote(wxCity);
        final String srcJs = jsQuote(wxSource);
        handler.post(new Runnable() {
            @Override public void run() {
                if (destroyed || web == null || !showingClock) return;
                web.evaluateJavascript("PCM_LOCATION(" + latJs + "," + lonJs + ","
                                       + cityJs + "," + srcJs + ")", null);
            }
        });
    }

    // ---------------- 天气（原生请求，绕开 WebView 跨域限制） ----------------

    private String weatherUrl() {
        double lat = Double.isNaN(wxLat) ? FALLBACK_LAT : wxLat;
        double lon = Double.isNaN(wxLon) ? FALLBACK_LON : wxLon;
        return "https://api.open-meteo.com/v1/forecast?latitude=" + lat + "&longitude=" + lon
            + "&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m"
            + "&daily=temperature_2m_max,temperature_2m_min"
            + "&timezone=auto&forecast_days=1";
    }

    private void scheduleWeather() {
        handler.removeCallbacks(weatherTask);
        handler.postDelayed(weatherTask, 900);
    }

    private final Runnable weatherTask = new Runnable() {
        @Override public void run() {
            if (destroyed || !showingClock) return;
            fetchWeather();
            handler.postDelayed(this, WEATHER_MS);
        }
    };

    private void fetchWeather() {
        final String target = weatherUrl();
        final String city = wxCity;
        new Thread(new Runnable() {
            @Override public void run() {
                String body = null;
                HttpURLConnection conn = null;
                try {
                    conn = (HttpURLConnection) new URL(target).openConnection();
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
                final String cityJs = jsQuote(city);
                handler.post(new Runnable() {
                    @Override public void run() {
                        if (destroyed || !showingClock || web == null) return;
                        web.evaluateJavascript("PCM_WEATHER(" + json + "," + cityJs + ")", null);
                    }
                });
            }
        }).start();
    }

    // ---------------- 重连探测 ----------------

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
        stopLocationUpdates();
        if (web != null) {
            web.loadUrl("about:blank");
            web.destroy();
        }
        super.onDestroy();
    }
}
