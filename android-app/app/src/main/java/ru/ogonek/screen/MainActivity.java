package ru.ogonek.screen;

import android.annotation.SuppressLint;
import android.app.AlertDialog;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowManager;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;
import androidx.appcompat.app.AppCompatActivity;

public class MainActivity extends AppCompatActivity {

    private WebView webView;
    private SharedPreferences prefs;
    private int tapCount = 0;
    private long lastTap = 0;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // Fullscreen + keep screen on
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
                | WindowManager.LayoutParams.FLAG_FULLSCREEN);
        getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_FULLSCREEN
                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY);

        prefs = getSharedPreferences("ogonek", MODE_PRIVATE);

        String serverUrl  = prefs.getString("server_url", "");
        int    cottageId  = prefs.getInt("cottage_id", 0);

        if (serverUrl.isEmpty() || cottageId == 0) {
            showSetupDialog(false);
            return;
        }

        startWebView(serverUrl, cottageId);
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void startWebView(String serverUrl, int cottageId) {
        setContentView(R.layout.activity_main);
        webView = findViewById(R.id.webView);

        WebSettings ws = webView.getSettings();
        ws.setJavaScriptEnabled(true);
        ws.setMediaPlaybackRequiresUserGesture(false);
        ws.setDomStorageEnabled(true);
        ws.setCacheMode(WebSettings.LOAD_DEFAULT);
        ws.setLoadWithOverviewMode(true);
        ws.setUseWideViewPort(true);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                // Retry in 10 seconds on any error
                webView.postDelayed(() -> webView.reload(), 10000);
            }
        });

        final String url = serverUrl + "/screen/" + cottageId;
        webView.loadUrl(url);

        // 5 taps in 3 seconds → open settings
        webView.setOnClickListener(v -> {
            long now = System.currentTimeMillis();
            if (now - lastTap > 3000) tapCount = 0;
            lastTap = now;
            tapCount++;
            if (tapCount >= 5) {
                tapCount = 0;
                showSetupDialog(true);
            }
        });
    }

    private void showSetupDialog(boolean canCancel) {
        String savedUrl  = prefs.getString("server_url", "http://72.56.245.146:8000");
        int    savedId   = prefs.getInt("cottage_id", 1);

        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(48, 32, 48, 16);

        TextView labelUrl = new TextView(this);
        labelUrl.setText("Адрес сервера:");
        labelUrl.setTextSize(16);
        layout.addView(labelUrl);

        EditText editUrl = new EditText(this);
        editUrl.setText(savedUrl);
        editUrl.setInputType(android.text.InputType.TYPE_CLASS_TEXT
                | android.text.InputType.TYPE_TEXT_VARIATION_URI);
        layout.addView(editUrl);

        TextView labelId = new TextView(this);
        labelId.setText("Номер домика (1–6):");
        labelId.setTextSize(16);
        labelId.setPadding(0, 24, 0, 0);
        layout.addView(labelId);

        EditText editId = new EditText(this);
        editId.setText(String.valueOf(savedId));
        editId.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);
        layout.addView(editId);

        AlertDialog.Builder builder = new AlertDialog.Builder(this)
                .setTitle("⚙️ Настройки Огонёк")
                .setView(layout)
                .setPositiveButton("Сохранить", (d, w) -> {
                    String url = editUrl.getText().toString().trim();
                    String idStr = editId.getText().toString().trim();
                    if (url.isEmpty() || idStr.isEmpty()) {
                        Toast.makeText(this, "Заполните все поля", Toast.LENGTH_SHORT).show();
                        return;
                    }
                    int id = Integer.parseInt(idStr);
                    if (id < 1 || id > 6) {
                        Toast.makeText(this, "Номер домика от 1 до 6", Toast.LENGTH_SHORT).show();
                        return;
                    }
                    prefs.edit()
                            .putString("server_url", url)
                            .putInt("cottage_id", id)
                            .apply();
                    recreate();
                });

        if (canCancel) {
            builder.setNegativeButton("Отмена", null);
        } else {
            builder.setCancelable(false);
        }

        builder.show();
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        // Disable back button
        if (keyCode == KeyEvent.KEYCODE_BACK) return true;
        return super.onKeyDown(keyCode, event);
    }

    @Override
    protected void onResume() {
        super.onResume();
        getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_FULLSCREEN
                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY);
    }
}
