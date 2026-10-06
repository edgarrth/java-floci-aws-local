package pe.axiz.settlement.adapters.out.postgresql;

import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.support.TransactionTemplate;
import pe.axiz.settlement.application.ports.out.LedgerPort;
import pe.axiz.settlement.domain.SettlementEvent;
import tools.jackson.databind.ObjectMapper;

@Repository
public class JdbcLedger implements LedgerPort {
  private final JdbcTemplate jdbc;
  private final TransactionTemplate tx;
  private final ObjectMapper json;

  public JdbcLedger(JdbcTemplate jdbc, TransactionTemplate tx, ObjectMapper json) {
    this.jdbc = jdbc;
    this.tx = tx;
    this.json = json;
  }

  @Override
  public boolean accept(SettlementEvent e) {
    return Boolean.TRUE.equals(
        tx.execute(
            s -> {
              int n =
                  jdbc.update(
                      "INSERT INTO inbox(event_id,payment_id) VALUES(?,?) ON CONFLICT(event_id) DO NOTHING",
                      e.eventId(),
                      e.paymentId());
              if (n == 0) return false;
              if (e.status().equals("AUTHORIZED")) {
                jdbc.update(
                    "INSERT INTO ledger(payment_id,merchant_id,amount,currency,account) VALUES(?,?,?,?,?)",
                    e.paymentId(),
                    e.merchantId(),
                    e.amount(),
                    e.currency(),
                    "ACQUIRER_RECEIVABLE");
                jdbc.update(
                    "INSERT INTO ledger(payment_id,merchant_id,amount,currency,account) VALUES(?,?,?,?,?)",
                    e.paymentId(),
                    e.merchantId(),
                    e.amount().negate(),
                    e.currency(),
                    "MERCHANT_PAYABLE");
              }
              jdbc.update(
                  "INSERT INTO receipt_outbox(payment_id,payload) VALUES(?,?)",
                  e.paymentId(),
                  json.writeValueAsString(e));
              return true;
            }));
  }
}
