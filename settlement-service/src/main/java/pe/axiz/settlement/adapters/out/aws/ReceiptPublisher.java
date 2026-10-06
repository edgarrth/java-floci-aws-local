package pe.axiz.settlement.adapters.out.aws;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.*;
import org.slf4j.*;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.core.env.Environment;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;
import pe.axiz.settlement.domain.SettlementEvent;
import software.amazon.awssdk.core.SdkBytes;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.kms.KmsClient;
import software.amazon.awssdk.services.kms.model.*;
import software.amazon.awssdk.services.s3.S3Client;
import tools.jackson.databind.ObjectMapper;

@Component
@ConditionalOnProperty(name = "axiz.jobs-enabled", havingValue = "true")
public class ReceiptPublisher {
  private static final Logger LOG = LoggerFactory.getLogger(ReceiptPublisher.class);
  private final JdbcTemplate jdbc;
  private final TransactionTemplate tx;
  private final S3Client s3;
  private final KmsClient kms;
  private final DynamoDbClient dynamo;
  private final ObjectMapper json;
  private final Environment env;

  record Entry(UUID id, String payload, int attempts) {}

  public ReceiptPublisher(
      JdbcTemplate j,
      TransactionTemplate tx,
      S3Client s,
      KmsClient k,
      DynamoDbClient d,
      ObjectMapper json,
      Environment env) {
    jdbc = j;
    this.tx = tx;
    s3 = s;
    kms = k;
    dynamo = d;
    this.json = json;
    this.env = env;
  }

  private AttributeValue str(String s) {
    return AttributeValue.builder().s(s).build();
  }

  @Scheduled(fixedDelay = 1000, initialDelay = 4000)
  public void publish() {
    var entries =
        tx.execute(
            status -> {
              var list =
                  jdbc.query(
                      "SELECT * FROM receipt_outbox WHERE completed_at IS NULL AND next_attempt_at<=CURRENT_TIMESTAMP ORDER BY next_attempt_at LIMIT 10 FOR UPDATE SKIP LOCKED",
                      (r, i) ->
                          new Entry(
                              r.getObject("payment_id", UUID.class),
                              r.getString("payload"),
                              r.getInt("attempts")));
              for (var e : list)
                jdbc.update(
                    "UPDATE receipt_outbox SET next_attempt_at=CURRENT_TIMESTAMP + INTERVAL '60 seconds' WHERE payment_id=?",
                    e.id());
              return list;
            });
    for (var e : entries)
      try {
        var event = json.readValue(e.payload(), SettlementEvent.class);
        byte[] bytes = e.payload().getBytes(StandardCharsets.UTF_8);
        var signature =
            kms.sign(
                b ->
                    b.keyId(env.getRequiredProperty("axiz.kms-key"))
                        .message(SdkBytes.fromByteArray(bytes))
                        .messageType(MessageType.RAW)
                        .signingAlgorithm(SigningAlgorithmSpec.RSASSA_PSS_SHA_256));
        String objectKey = "receipts/" + e.id() + ".json";
        // La clave determinista permite repetir una entrega después de una caída sin crear otro
        // comprobante lógico.
        s3.putObject(
            b ->
                b.bucket(env.getRequiredProperty("axiz.bucket"))
                    .key(objectKey)
                    .contentType("application/json")
                    .metadata(
                        Map.of(
                            "signature",
                            Base64.getEncoder().encodeToString(signature.signature().asByteArray()),
                            "key-id",
                            signature.keyId())),
            RequestBody.fromBytes(bytes));
        dynamo.putItem(
            b ->
                b.tableName(env.getRequiredProperty("axiz.projection-table"))
                    .item(
                        Map.of(
                            "pk",
                            str(e.id().toString()),
                            "status",
                            str(event.status()),
                            "receiptKey",
                            str(objectKey),
                            "amount",
                            AttributeValue.builder().n(event.amount().toPlainString()).build(),
                            "currency",
                            str(event.currency()))));
        jdbc.update(
            "UPDATE receipt_outbox SET completed_at=CURRENT_TIMESTAMP WHERE payment_id=?", e.id());
      } catch (RuntimeException ex) {
        int wait = Math.min(60, 1 << Math.min(6, e.attempts()));
        jdbc.update(
            "UPDATE receipt_outbox SET attempts=attempts+1,next_attempt_at=? WHERE payment_id=?",
            java.sql.Timestamp.from(Instant.now().plusSeconds(wait)),
            e.id());
        LOG.warn("Se reintentará el comprobante del pago {}", e.id());
      }
  }
}
