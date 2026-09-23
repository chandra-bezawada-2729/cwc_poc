package ai.avenirdigital.cwc;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;
import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * {@code @EnableScheduling} is required for {@link
 * ai.avenirdigital.cwc.service.InboundScannerService}'s {@code @Scheduled} poll.
 * Without it Spring registers the bean and runs nothing — the scanner would look
 * healthy in the logs at startup and then never fire, which is a much worse
 * failure than not starting at all.
 */
@SpringBootApplication
@ConfigurationPropertiesScan
@EnableAsync
@EnableScheduling
public class CwcApplication {

    public static void main(String[] args) {
        SpringApplication.run(CwcApplication.class, args);
    }
}
