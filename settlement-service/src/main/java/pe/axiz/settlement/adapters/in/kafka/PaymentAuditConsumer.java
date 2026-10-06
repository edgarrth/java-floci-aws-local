package pe.axiz.settlement.adapters.in.kafka;

import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import org.apache.kafka.clients.consumer.*;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.core.env.Environment;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import pe.axiz.platform.configuration.KafkaEndpoint;
import pe.axiz.settlement.application.ports.out.AuditPort;
import pe.axiz.settlement.domain.PaymentAudit;
import pe.axiz.settlement.domain.SettlementEvent;
import tools.jackson.databind.ObjectMapper;

@Component
@ConditionalOnProperty(name = "axiz.jobs-enabled", havingValue = "true")
public class PaymentAuditConsumer implements AutoCloseable {
  private static final Logger LOG = LoggerFactory.getLogger(PaymentAuditConsumer.class);
  private final KafkaConsumer<String, String> consumer;
  private final AuditPort audit;
  private final ObjectMapper json;
  private boolean closed;

  public PaymentAuditConsumer(AuditPort audit, ObjectMapper json, Environment environment) {
    this.audit = audit;
    this.json = json;
    var properties = new Properties();
    properties.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, KafkaEndpoint.resolve(environment));
    properties.put(ConsumerConfig.GROUP_ID_CONFIG, "axiz-payment-audit");
    properties.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
    properties.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
    properties.put(ConsumerConfig.ENABLE_AUTO_COMMIT_CONFIG, false);
    properties.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");
    properties.put(ConsumerConfig.MAX_POLL_RECORDS_CONFIG, 20);
    properties.put(ConsumerConfig.DEFAULT_API_TIMEOUT_MS_CONFIG, 5000);
    consumer = new KafkaConsumer<>(properties);
    consumer.subscribe(List.of(environment.getRequiredProperty("axiz.kafka-topic")));
  }

  @Scheduled(fixedDelay = 500, initialDelay = 4000)
  public synchronized void consume() {
    if (closed) return;
    try {
      var records = consumer.poll(Duration.ofMillis(500));
      for (var partition : records.partitions()) {
        for (var record : records.records(partition)) {
          try {
            var event = json.readValue(record.value(), SettlementEvent.class);
            audit.record(
                new PaymentAudit(
                    event.eventId(), record.value(), record.partition(), record.offset()));
            consumer.commitSync(Map.of(partition, new OffsetAndMetadata(record.offset() + 1)));
          } catch (RuntimeException exception) {
            // Se conserva la posición del evento fallido; las demás particiones pueden continuar.
            consumer.seek(partition, record.offset());
            LOG.warn(
                "Se reintentará el evento Kafka {}:{}: {}",
                record.partition(),
                record.offset(),
                exception.getClass().getSimpleName());
            break;
          }
        }
      }
    } catch (RuntimeException exception) {
      LOG.warn("Se reintentará la auditoría Kafka: {}", exception.getClass().getSimpleName());
    }
  }

  @Override
  public synchronized void close() {
    closed = true;
    consumer.close(CloseOptions.timeout(Duration.ofSeconds(5)));
  }
}
