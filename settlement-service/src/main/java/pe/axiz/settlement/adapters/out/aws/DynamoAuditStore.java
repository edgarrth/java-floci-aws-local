package pe.axiz.settlement.adapters.out.aws;

import java.util.Map;
import java.util.Optional;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Repository;
import pe.axiz.settlement.application.ports.out.AuditPort;
import pe.axiz.settlement.domain.PaymentAudit;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.dynamodb.model.ConditionalCheckFailedException;

@Repository
public class DynamoAuditStore implements AuditPort {
  private final DynamoDbClient dynamo;
  private final String table;

  public DynamoAuditStore(DynamoDbClient dynamo, Environment environment) {
    this.dynamo = dynamo;
    this.table = environment.getRequiredProperty("axiz.audit-table");
  }

  @Override
  public void record(PaymentAudit audit) {
    try {
      dynamo.putItem(
          request ->
              request
                  .tableName(table)
                  .conditionExpression("attribute_not_exists(pk)")
                  .item(
                      Map.of(
                          "pk",
                          text(audit.eventId()),
                          "payload",
                          text(audit.payload()),
                          "partition",
                          number(audit.partition()),
                          "offset",
                          number(audit.offset()))));
    } catch (ConditionalCheckFailedException exception) {
      var existing = find(audit.eventId()).orElseThrow();
      if (!existing.payload().equals(audit.payload()))
        throw new IllegalStateException(
            "El evento de auditoría ya existe con otro contenido", exception);
    }
  }

  @Override
  public Optional<PaymentAudit> find(String eventId) {
    var item =
        dynamo
            .getItem(
                request ->
                    request.tableName(table).key(Map.of("pk", text(eventId))).consistentRead(true))
            .item();
    if (item.isEmpty()) return Optional.empty();
    return Optional.of(
        new PaymentAudit(
            eventId,
            item.get("payload").s(),
            Integer.parseInt(item.get("partition").n()),
            Long.parseLong(item.get("offset").n())));
  }

  private AttributeValue text(String value) {
    return AttributeValue.builder().s(value).build();
  }

  private AttributeValue number(long value) {
    return AttributeValue.builder().n(Long.toString(value)).build();
  }
}
