package ai.avenirdigital.cwc.controller;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;

@RestController
@RequestMapping("/api")
public class HealthController {

    @Value("${ai.service.url:http://127.0.0.1:5002}")
    private String aiServiceUrl;

    @Value("${spring.application.name:cwc-backend}")
    private String applicationName;

    @GetMapping("/health")
    public ResponseEntity<Map<String, Object>> health() {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", "UP");
        body.put("service", applicationName);
        body.put("version", "0.1.0-SNAPSHOT");
        body.put("timestamp", Instant.now().toString());
        body.put("ai_service_url", aiServiceUrl);
        return ResponseEntity.ok(body);
    }
}
