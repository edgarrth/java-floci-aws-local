package pe.axiz.payment.adapters.out.aws;

import java.time.*;
import java.util.*;
import java.util.concurrent.*;
import org.apache.kafka.clients.producer.*;
import org.apache.kafka.common.serialization.StringSerializer;
import org.slf4j.*;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.core.env.Environment;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;
import pe.axiz.payment.configuration.ResiliencePolicies;
import software.amazon.awssdk.services.eventbridge.EventBridgeClient;
import software.amazon.awssdk.services.eventbridge.model.PutEventsRequestEntry;
import software.amazon.awssdk.services.sns.SnsClient;

@Component
@ConditionalOnProperty(name = "axiz.jobs-enabled", havingValue = "true")
public class OutboxPublisher implements AutoCloseable {
  private static final Logger LOG = LoggerFactory.getLogger(OutboxPublisher.class);
  private final JdbcTemplate jdbc;
  private final TransactionTemplate tx;
  private final SnsClient sns;
  private final EventBridgeClient events;
  private final ResiliencePolicies resilience;
  private final Environment env;
  private final KafkaProducer<String, String> kafka;

  record Entry(UUID id, UUID payment, String destination, String payload, int attempts) {}

  public OutboxPublisher(
      JdbcTemplate jdbc,
      TransactionTemplate tx,
      SnsClient sns,
      EventBridgeClient events,
      ResiliencePolicies resilience,
      Environment env) {
    this.jdbc = jdbc;
    this.tx = tx;
    this.sns = sns;
    this.events = events;
    this.resilience = resilience;
    this.env = env;
    Properties p = new Properties();
    String bootstrap = env.getRequiredProperty("axiz.kafka-bootstrap");
    String bootstrapFile = env.getProperty("axiz.kafka-bootstrap-file", "");
    if (!bootstrapFile.isBlank()) {
      try {
        bootstrap = java.nio.file.Files.readString(java.nio.file.Path.of(bootstrapFile)).trim();
      } catch (java.io.IOException ex) {
        throw new IllegalStateException("No se pudo leer el endpoint MSK", ex);
      }
    }
    p.put(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrap);
    p.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
    p.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
    p.put(ProducerConfig.ENABLE_IDEMPOTENCE_CONFIG, true);
    p.put(ProducerConfig.ACKS_CONFIG, "all");
    p.put(ProducerConfig.DELIVERY_TIMEOUT_MS_CONFIG, 8000);
    p.put(ProducerConfig.REQUEST_TIMEOUT_MS_CONFIG, 3000);
    p.put(ProducerConfig.MAX_BLOCK_MS_CONFIG, 5000);
    kafka = new KafkaProducer<>(p);
  }

  @Scheduled(fixedDelay = 1000, initialDelay = 3000)
  public void publish() {
    List<Entry> batch =
        tx.execute(
            s -> {
              var list =
                  jdbc.query(
                      "SELECT * FROM outbox WHERE published_at IS NULL AND next_attempt_at<=CURRENT_TIMESTAMP ORDER BY next_attempt_at LIMIT 8 FOR UPDATE SKIP LOCKED",
                      (r, i) ->
                          new Entry(
                              r.getObject("id", UUID.class),
                              r.getObject("payment_id", UUID.class),
                              r.getString("destination"),
                              r.getString("payload"),
                              r.getInt("attempts")));
              for (var e : list)
                jdbc.update(
                    "UPDATE outbox SET next_attempt_at=CURRENT_TIMESTAMP + INTERVAL '60 seconds' WHERE id=?",
                    e.id());
              return list;
            });
    for (var e : batch) {
      try {
        resilience
            .publisher
            .executeSupplier(
                () -> {
                  deliver(e);
                  return true;
                })
            .toCompletableFuture()
            .get(12, TimeUnit.SECONDS);
        jdbc.update("UPDATE outbox SET published_at=CURRENT_TIMESTAMP WHERE id=?", e.id());
      } catch (Exception ex) {
        int delay = Math.min(60, 1 << Math.min(6, e.attempts()));
        jdbc.update(
            "UPDATE outbox SET attempts=attempts+1,next_attempt_at=? WHERE id=?",
            java.sql.Timestamp.from(Instant.now().plusSeconds(delay)),
            e.id());
        LOG.warn("Se reintentará la entrega del evento {} a {}", e.id(), e.destination());
      }
    }
  }

  private void deliver(Entry e) {
    switch (e.destination()) {
      case "SNS" ->
          sns.publish(
              b ->
                  b.topicArn(env.getRequiredProperty("axiz.topic-arn"))
                      .message(e.payload())
                      .messageGroupId(e.payment().toString())
                      .messageDeduplicationId(e.id().toString()));
      case "KAFKA" -> {
        try {
          kafka
              .send(
                  new ProducerRecord<>(
                      env.getRequiredProperty("axiz.kafka-topic"),
                      e.payment().toString(),
                      e.payload()))
              .get(8, TimeUnit.SECONDS);
        } catch (InterruptedException ex) {
          Thread.currentThread().interrupt();
          throw new IllegalStateException(ex);
        } catch (Exception ex) {
          throw new IllegalStateException(ex);
        }
      }
      case "EVENTBRIDGE" -> {
        var response =
            events.putEvents(
                b ->
                    b.entries(
                        PutEventsRequestEntry.builder()
                            .eventBusName(env.getRequiredProperty("axiz.event-bus"))
                            .source("pe.axiz.payments")
                            .detailType("PaymentFinalized")
                            .detail(e.payload())
                            .build()));
        if (response.failedEntryCount() > 0)
          throw new IllegalStateException("EventBridge rechazó el evento");
      }
      default -> throw new IllegalArgumentException("Destino desconocido");
    }
  }

  @Override
  public void close() {
    kafka.close(Duration.ofSeconds(5));
  }
}
