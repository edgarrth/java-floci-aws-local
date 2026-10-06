package pe.axiz.payment.adapters.out.postgresql;

import java.sql.Timestamp;
import java.util.*;
import org.springframework.jdbc.core.*;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.support.TransactionTemplate;
import pe.axiz.payment.application.ports.out.PaymentStore;
import pe.axiz.payment.domain.*;
import tools.jackson.databind.ObjectMapper;

@Repository
public class JdbcPaymentStore implements PaymentStore {
  private final JdbcTemplate jdbc;
  private final TransactionTemplate tx;
  private final ObjectMapper json;

  public JdbcPaymentStore(JdbcTemplate jdbc, TransactionTemplate tx, ObjectMapper json) {
    this.jdbc = jdbc;
    this.tx = tx;
    this.json = json;
  }

  private final RowMapper<Payment> mapper =
      (result, index) ->
          new Payment(
              result.getObject("id", UUID.class),
              result.getString("merchant_id"),
              new Money(result.getBigDecimal("amount"), result.getString("currency")),
              result.getString("token"),
              result.getString("idempotency_key"),
              result.getString("fingerprint"),
              Payment.Status.valueOf(result.getString("status")),
              result.getString("reason"),
              result.getTimestamp("created_at").toInstant());

  @Override
  public Payment record(Payment payment) {
    return tx.execute(
        transaction -> {
          int inserted =
              jdbc.update(
                  "INSERT INTO payments(id,merchant_id,amount,currency,token,idempotency_key,fingerprint,status,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(merchant_id,idempotency_key) DO NOTHING",
                  payment.id(),
                  payment.merchantId(),
                  payment.money().amount(),
                  payment.money().currency(),
                  payment.token(),
                  payment.idempotencyKey(),
                  payment.fingerprint(),
                  payment.status().name(),
                  payment.reason(),
                  Timestamp.from(payment.createdAt()));
          if (inserted == 1) {
            var event = new LinkedHashMap<String, Object>();
            event.put("eventId", payment.id() + "-final");
            event.put("paymentId", payment.id());
            event.put("merchantId", payment.merchantId());
            event.put("amount", payment.money().amount());
            event.put("currency", payment.money().currency());
            event.put("status", payment.status());
            event.put("reason", payment.reason());
            event.put("schemaVersion", 1);
            String payload = json.writeValueAsString(event);
            for (String sink : List.of("SNS", "KAFKA"))
              jdbc.update(
                  "INSERT INTO outbox(id,payment_id,destination,payload) VALUES(?,?,?,?)",
                  UUID.randomUUID(),
                  payment.id(),
                  sink,
                  payload);
          }
          return findByKey(payment.merchantId(), payment.idempotencyKey()).orElseThrow();
        });
  }

  @Override
  public Optional<Payment> find(UUID id) {
    return jdbc.query("SELECT * FROM payments WHERE id=?", mapper, id).stream().findFirst();
  }

  @Override
  public Optional<Payment> findByKey(String merchantId, String key) {
    return jdbc
        .query(
            "SELECT * FROM payments WHERE merchant_id=? AND idempotency_key=?",
            mapper,
            merchantId,
            key)
        .stream()
        .findFirst();
  }
}
