package pe.axiz.platform.configuration;

import io.micrometer.core.instrument.MeterRegistry;
import org.slf4j.*;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.core.env.Environment;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import software.amazon.awssdk.services.cloudwatch.CloudWatchClient;
import software.amazon.awssdk.services.cloudwatch.model.*;

@Component
@ConditionalOnProperty(name = "axiz.jobs-enabled", havingValue = "true")
public class CloudWatchMetricsPublisher {
  private static final Logger LOG = LoggerFactory.getLogger(CloudWatchMetricsPublisher.class);
  private final CloudWatchClient cloudWatch;
  private final MeterRegistry metrics;
  private final String service;
  private long previousCount;

  public CloudWatchMetricsPublisher(CloudWatchClient c, MeterRegistry m, Environment e) {
    cloudWatch = c;
    metrics = m;
    service = e.getRequiredProperty("spring.application.name");
  }

  @Scheduled(fixedDelay = 30000, initialDelay = 30000)
  public void publish() {
    var total =
        metrics.find("http.server.requests").timers().stream().mapToLong(t -> t.count()).sum();
    var counter = Math.max(0, total - previousCount);
    try {
      cloudWatch.putMetricData(
          b ->
              b.namespace("Axiz/Payments")
                  .metricData(
                      MetricDatum.builder()
                          .metricName("HttpRequests")
                          .value((double) counter)
                          .unit(StandardUnit.COUNT)
                          .dimensions(Dimension.builder().name("Service").value(service).build())
                          .build()));
      previousCount = total;
    } catch (RuntimeException ex) {
      LOG.warn("Se reintentará la publicación de métricas de {}", service);
    }
  }
}
